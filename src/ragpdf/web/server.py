"""Server locale dell'interfaccia grafica: ``ragpdf ui``.

Espone delle API JSON sopra la stessa logica usata dalla riga di comando (``App``,
``SetupSession``, ``Extractor``) e serve la pagina web in ``static/``. Ascolta solo su
127.0.0.1: è raggiungibile solo da questo computer, e le chiavi API restano nel file .env.

Tutto è organizzato per **progetto** (uno o più PDF con la stessa struttura, un CSV unico).

Regole dell'interfaccia, garantite anche qui:

- niente parte verso Gemini se non da un endpoint esplicito (``.../setup/start``,
  ``.../setup/revise``, ``.../setup/preview``, ``.../index``), che la pagina chiama solo
  dopo una conferma dell'utente;
- le operazioni lunghe sono *lavori* in background (vedi ``jobs.py``), uno per progetto;
- i prompt si possono vedere prima dell'invio (``.../setup/prompt``).
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ragpdf import __version__
from ragpdf.app import App, PrimerMissingError
from ragpdf.core.documents import Document, DocumentError, DuplicateDocumentError
from ragpdf.core.gemini import GeminiError, load_api_keys
from ragpdf.core.library import PAGE_TAGS, FieldDef, LibraryCard
from ragpdf.core.manifest import DONE, Manifest
from ragpdf.core.page_runner import RunControl
from ragpdf.core.pdf_tools import has_text_layer, render_thumbnail
from ragpdf.core.projects import DocumentSettings, Project, ProjectError, clean_columns, compare_columns
from ragpdf.core.render import format_pages, parse_page_ranges, render_pages
from ragpdf.core.workspace import slugify
from ragpdf.indexing.extractor import Extractor, SpecChangedError, selected_pages
from ragpdf.indexing.spec import SpecError, read_csv_rows
from ragpdf.library_analysis import (
    HEAVY_PDF_BYTES,
    PromptStore,
    Selection,
    apply_analysis,
    attachment_size,
    build_prompt,
    check_pages,
    get_analysis,
    list_analyses,
    quick_pages,
    run_analysis,
    save_analysis,
)
from ragpdf.library_grouping import (
    MAX_CATALOGS,
    MAX_CATALOGS_WITH_IMAGES,
    Grouping,
    apply_group_tags,
    create_group_project,
    get_grouping,
    is_studied,
    list_groupings,
    move_document,
    prepare,
    run_grouping,
    save_grouping,
    update_group,
)
from ragpdf.primer import MAX_SELECTED_PAGES
from ragpdf.setup_session import (
    SetupError,
    SetupSession,
    default_context_refs,
    format_page_refs,
    parse_page_refs,
)
from ragpdf.web.jobs import JobConflictError, JobManager

STATIC_DIR = Path(__file__).parent / "static"
MAX_ROWS_IN_VIEW = 300
# l'interfaccia cambia a ogni aggiornamento del programma: il browser deve sempre ricontrollare
# i file (risposta "304 non modificato" se uguali), altrimenti può mescolare versioni diverse
NO_CACHE = {"Cache-Control": "no-cache"}


class _UiFiles(StaticFiles):
    """File statici dell'interfaccia, sempre ricontrollati dal browser."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE)
        return response


def create_app(app: App, jobs: JobManager | None = None) -> FastAPI:
    api = FastAPI(title="ragpdf", version=__version__, docs_url=None, redoc_url=None)
    jobs = jobs or JobManager()
    api.state.app = app
    api.state.jobs = jobs

    # ------------------------------------------------------------------ errori
    def _error(status: int, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse({"detail": message, **extra}, status_code=status)

    @api.exception_handler(DuplicateDocumentError)
    async def _dup(_: Request, e: DuplicateDocumentError):
        return _error(409, str(e), existing=e.existing.id)

    @api.exception_handler(DocumentError)
    async def _doc(_: Request, e: DocumentError):
        return _error(404 if "non trovato" in str(e) else 400, str(e))

    @api.exception_handler(ProjectError)
    async def _proj(_: Request, e: ProjectError):
        return _error(404 if "non trovato" in str(e) else 400, str(e))

    @api.exception_handler(SpecChangedError)
    async def _changed(_: Request, e: SpecChangedError):
        return _error(409, str(e) + " Per applicare le regole attuali rifai l'indicizzazione da capo.",
                      spec_changed=True)

    @api.exception_handler(JobConflictError)
    async def _conflict(_: Request, e: JobConflictError):
        return _error(409, str(e))

    for exc_type in (SpecError, SetupError, PrimerMissingError, GeminiError, ValueError):
        @api.exception_handler(exc_type)
        async def _bad(_: Request, e: Exception):
            return _error(400, str(e))

    @api.exception_handler(FileNotFoundError)
    async def _missing(_: Request, e: FileNotFoundError):
        return _error(404, str(e))

    # ------------------------------------------------------------------ utility
    def project_or_404(pid: str) -> Project:
        return app.projects.get(pid)

    def doc_of(project: Project, doc_id: str) -> Document:
        if doc_id not in project.info.documents:
            raise HTTPException(404, f"Il documento '{doc_id}' non fa parte del progetto.")
        return app.documents.get(doc_id)

    def session_or_404(project: Project) -> SetupSession:
        session = SetupSession.load(app, project)
        if session is None:
            raise HTTPException(404, "Nessuna configurazione guidata per questo progetto.")
        return session

    def no_job(project: Project) -> None:
        if jobs.active_for(project.id):
            raise HTTPException(409, "C'è un'operazione in corso su questo progetto.")

    def usage() -> dict[str, Any] | None:
        u = getattr(app._client, "usage", None)
        if u is None:
            return None
        return {"requests": u.requests, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "text": u.describe()}

    def refs_from_body(value: Any, docs: list[Document]) -> list[tuple[str, int]]:
        """Pagine come lista ``[[doc, pagina], ...]`` oppure testo ``"alfa: 1-3; beta: 2"``."""
        if isinstance(value, list):
            ids = {d.id: d for d in docs}
            refs = []
            for item in value:
                doc_id, page = str(item[0]), int(item[1])
                if doc_id not in ids or not 1 <= page <= ids[doc_id].info.page_count:
                    raise ValueError(f"Pagina non valida: {doc_id} p.{page}")
                if (doc_id, page) not in refs:
                    refs.append((doc_id, page))
            return refs
        return parse_page_refs(str(value or ""), docs)

    def doc_status(project: Project, doc: Document) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": doc.id, "title": doc.info.title, "source_filename": doc.info.source_filename,
            "page_count": doc.info.page_count, "settings": project.settings_for(doc.id).model_dump(),
            "done": 0, "failed": 0,
        }
        card = app.library.card(doc)
        info["library"] = {"description": card.description, "data": [f.name for f in card.fields],
                           "collections": len(card.collections), "folder": card.folder, "tags": card.tags}
        if project.info.spec:
            manifest_path = doc.extraction_dir(project.info.spec) / "manifest.json"
            if manifest_path.exists():
                c = Manifest.load_or_create(manifest_path, doc.info.page_count).counts()
                info.update(done=c.get(DONE, 0), failed=c.get("failed", 0))
        return info

    def spec_columns_check(project: Project) -> dict[str, Any] | None:
        """Confronto tra le colonne del progetto e quelle della specifica salvata (se ci sono entrambe)."""
        if not project.info.fields or not project.info.spec:
            return None
        try:
            spec = app.project_spec(project)
        except (SpecError, FileNotFoundError):
            return None
        return compare_columns(project.info.column_names, [c.name for c in spec.columns])

    def project_summary(project: Project) -> dict[str, Any]:
        docs = app.project_documents(project)
        session = SetupSession.load(app, project)
        job = jobs.active_for(project.id)
        statuses = [doc_status(project, d) for d in docs]
        return {
            **project.info.model_dump(exclude={"settings"}),
            "has_table": project.info.has_table,
            "columns_check": spec_columns_check(project),
            "documents": statuses,
            "page_count": sum(d.info.page_count for d in docs),
            "done": sum(s["done"] for s in statuses),
            "setup": None if session is None else {
                "round": session.state.current_round, "saved": bool(session.state.saved_at),
            },
            "job": job.to_dict() if job else None,
        }

    # ------------------------------------------------------------------ stato
    @api.get("/api/status")
    def status():
        keys = load_api_keys()
        return {
            "version": __version__,
            "simulated": app.simulated,
            "keys": len(keys),
            "model": app.config.gemini.model,
            "input_mode": app.config.indexing.input,
            "workspace": str(app.workspace.root),
            "usage": usage(),
            "ready": bool(keys) or app.simulated,
        }

    @api.get("/api/library")
    def library():
        return {"primers": app.workspace.list_primers(), "specs": app.workspace.list_specs()}

    # ------------------------------------------------------------------ progetti
    @api.get("/api/projects")
    def list_projects():
        return [project_summary(p) for p in app.projects.list()]

    @api.post("/api/projects")
    def create_project(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("Dai un nome al progetto.")
        return project_summary(app.projects.create(name))

    @api.get("/api/projects/{pid}")
    def get_project(pid: str):
        return project_summary(project_or_404(pid))

    @api.put("/api/projects/{pid}/settings")
    def project_settings(pid: str, body: dict = Body(...)):
        """Impostazioni globali: descrizione, cosa è una riga, colonne, altre indicazioni."""
        project = project_or_404(pid)
        info = project.info
        if "name" in body and str(body["name"] or "").strip():
            info.name = " ".join(str(body["name"]).split())
        if "description" in body:
            info.description = str(body["description"] or "").strip()
        if "row" in body:
            info.row = " ".join(str(body["row"] or "").split())
        if "columns" in body:
            info.fields = clean_columns(body["columns"] if isinstance(body["columns"], list) else [])
        if "instructions" in body:
            info.instructions = str(body["instructions"] or "").strip()
        project.save()
        return project_summary(project)

    @api.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        project = project_or_404(pid)
        no_job(project)
        app.remove_project(project)
        return {"deleted": project.id}

    @api.post("/api/projects/{pid}/documents")
    def upload_document(pid: str, file: UploadFile = File(...)):
        project = project_or_404(pid)
        name = Path(file.filename or "documento.pdf").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "Il file deve essere un PDF.")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / name
            with open(target, "wb") as out:
                shutil.copyfileobj(file.file, out)
            try:
                doc = app.add_document(target, project=project)
                already = False
            except DuplicateDocumentError as e:
                doc = app.documents.get(e.existing.id)
                app.projects.add_document(project, doc.id)
                already = True
        result = doc_status(app.projects.get(project.id), doc)
        result.update(scanned=not has_text_layer(doc.pdf_path), already_present=already)
        return result

    @api.delete("/api/projects/{pid}/documents/{doc_id}")
    def remove_document(pid: str, doc_id: str):
        project = project_or_404(pid)
        no_job(project)
        doc = doc_of(project, doc_id)
        app.remove_from_project(project, doc)
        return {"removed": doc.id}

    @api.put("/api/projects/{pid}/documents/{doc_id}/settings")
    def document_settings(pid: str, doc_id: str, body: dict = Body(...)):
        project = project_or_404(pid)
        doc = doc_of(project, doc_id)
        pages = str(body.get("pages") or "").strip() or None
        if pages:
            parse_page_ranges(pages, doc.info.page_count)  # errore leggibile se non valide
        project.set_settings(doc.id, DocumentSettings(notes=str(body.get("notes") or ""), pages=pages))
        project.save()
        return doc_status(project, doc)

    # ------------------------------------------------------------------ biblioteca
    def upload_to_temp(file: UploadFile, tmp: str) -> Path:
        name = Path(file.filename or "documento.pdf").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, f"{name}: il file deve essere un PDF.")
        target = Path(tmp) / name
        with open(target, "wb") as out:
            shutil.copyfileobj(file.file, out)
        return target

    def catalog_summary(doc: Document, card: LibraryCard, projects: list[Project]) -> dict[str, Any]:
        return {
            "id": doc.id, "title": doc.info.title, "source_filename": doc.info.source_filename,
            "page_count": doc.info.page_count, "added_at": doc.info.added_at,
            "folder": card.folder, "tags": card.tags, "description": card.description,
            "fields": len(card.fields), "collections": len(card.collections),
            "to_index": len(card.pages_with_tag("dati")),
            "proposals": sum(1 for a in list_analyses(doc) if a.status == "nuova"),
            "projects": [{"id": p.id, "name": p.info.name} for p in projects if doc.id in p.info.documents],
        }

    def docs_from_body(body: dict) -> list[Document]:
        ids = body.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ValueError("Seleziona almeno un catalogo.")
        return [app.documents.get(str(i)) for i in ids]

    @api.get("/api/library/catalogs")
    def library_overview():
        """Tutta la biblioteca in una risposta: filtri e ricerca li fa il browser."""
        projects = app.projects.list()
        docs = app.documents.list()
        cards = [app.library.card(d) for d in docs]
        tags: dict[str, int] = {}
        for card in cards:
            for t in card.tags:
                key = next((k for k in tags if k.casefold() == t.casefold()), t)
                tags[key] = tags.get(key, 0) + 1
        return {
            "folders": app.library.folders(cards),
            "tags": [{"name": k, "count": v} for k, v in sorted(tags.items(), key=lambda x: x[0].casefold())],
            "page_tags": [{"id": k, "label": v[0], "help": v[1]} for k, v in PAGE_TAGS.items()],
            "catalogs": [catalog_summary(d, c, projects) for d, c in zip(docs, cards, strict=True)],
        }

    @api.post("/api/library/catalogs")
    def library_upload(file: UploadFile = File(...), folder: str = Form("")):
        with tempfile.TemporaryDirectory() as tmp:
            target = upload_to_temp(file, tmp)
            try:
                doc = app.add_to_library(target, folder=folder)
                already = False
            except DuplicateDocumentError as e:
                doc = app.documents.get(e.existing.id)
                already = True
        card = app.library.card(doc)
        result = catalog_summary(doc, card, app.projects.list())
        result.update(already_present=already, scanned=not already and not has_text_layer(doc.pdf_path))
        return result

    @api.get("/api/library/catalogs/{doc_id}")
    def library_catalog(doc_id: str):
        doc = app.documents.get(doc_id)
        card = app.library.card(doc)
        return {**catalog_summary(doc, card, app.projects.list()), **card.model_dump(),
                "scanned": not has_text_layer(doc.pdf_path), "size_mb": round(doc.pdf_path.stat().st_size / 2**20, 1)}

    @api.put("/api/library/catalogs/{doc_id}")
    def library_update(doc_id: str, body: dict = Body(...)):
        """Aggiorna titolo, descrizione, tag, campi e cartella (solo le voci presenti)."""
        doc = app.documents.get(doc_id)
        card = app.library.card(doc)
        if "title" in body:
            title = " ".join(str(body["title"] or "").split())
            if not title:
                raise ValueError("Il titolo non può essere vuoto.")
            doc.info.title = title
            doc.save()
        if "description" in body:
            card.description = str(body["description"] or "").strip()
        if "tags" in body:
            card.tags = [str(t) for t in body["tags"] or []]
        if "folder" in body:
            card.folder = str(body["folder"] or "")
        if "fields" in body:
            fields = [FieldDef.model_validate(f) for f in body["fields"] or [] if str(f.get("name", "")).strip()]
            names = [f.name.casefold() for f in fields]
            if len(names) != len(set(names)):
                raise ValueError("Ci sono due campi con lo stesso nome.")
            card.fields = fields
        app.library.save_card(doc, card)
        return library_catalog(doc_id)

    # ------------------------------------------------------------------ analisi con Gemini
    prompts = PromptStore(app.workspace)

    def analysis_job_key(doc: Document) -> str:
        return f"lib:{doc.id}"

    def analysis_request(doc: Document, body: dict) -> tuple[list[int] | None, str, bool]:
        if body.get("whole"):
            pages = None
        else:   # attenzione: per parse_page_ranges il testo vuoto vuol dire "tutte le pagine"
            pages = parse_page_ranges(str(body.get("pages") or ""), doc.info.page_count) or []
        return check_pages(doc, pages), str(body.get("instructions") or ""), bool(body.get("include_card", True))

    def analysis_view(a) -> dict[str, Any]:
        return {**a.model_dump(), "pages_text": a.pages_text}

    @api.get("/api/library/analysis-prompt")
    def analysis_prompt():
        return {"factory": prompts.factory(), "custom": prompts.custom(), "effective": prompts.effective()}

    @api.put("/api/library/analysis-prompt")
    def analysis_prompt_save(body: dict = Body(...)):
        prompts.save(str(body.get("text") or ""))
        return analysis_prompt()

    @api.get("/api/library/catalogs/{doc_id}/quick-pages")
    def analysis_quick_pages(doc_id: str):
        doc = app.documents.get(doc_id)
        return {"pages": format_pages(quick_pages(doc.info.page_count))}

    @api.post("/api/library/catalogs/{doc_id}/analysis/preview")
    def analysis_preview(doc_id: str, body: dict = Body(...)):
        """Prompt esatto e dimensione dell'allegato, senza chiamare Gemini."""
        doc = app.documents.get(doc_id)
        pages, instructions, include_card = analysis_request(doc, body)
        size = attachment_size(doc, pages)
        return {
            "prompt": build_prompt(doc, app.library.card(doc), instructions, pages, include_card),
            "pages": doc.info.page_count if pages is None else len(pages),
            "pages_text": "PDF intero" if pages is None else format_pages(pages),
            "size_mb": round(size / 2**20, 1), "heavy": size > HEAVY_PDF_BYTES,
        }

    @api.post("/api/library/catalogs/{doc_id}/analysis")
    def analysis_start(doc_id: str, body: dict = Body(...)):
        doc = app.documents.get(doc_id)
        pages, instructions, include_card = analysis_request(doc, body)
        client = app.client()   # errore subito se mancano le chiavi

        def work(job):
            def progress(message: str) -> None:
                job.control.progress(0, 0, message)
            analysis = run_analysis(doc, app.library, client, instructions, pages, include_card, progress)
            return {"analysis_id": analysis.id, "problems": analysis.problems}

        label = f"Gemini analizza '{doc.info.title}'"
        return jobs.submit(analysis_job_key(doc), "analysis", label, work).to_dict()

    @api.get("/api/library/catalogs/{doc_id}/job")
    def analysis_job(doc_id: str):
        job = jobs.active_for(analysis_job_key(app.documents.get(doc_id)))
        return job.to_dict() if job else None

    @api.get("/api/library/catalogs/{doc_id}/analyses")
    def analyses(doc_id: str):
        return [analysis_view(a) for a in list_analyses(app.documents.get(doc_id))]

    @api.post("/api/library/catalogs/{doc_id}/analyses/{analysis_id}/apply")
    def analysis_apply(doc_id: str, analysis_id: str, body: dict = Body(...)):
        doc = app.documents.get(doc_id)
        apply_analysis(doc, app.library, get_analysis(doc, analysis_id), Selection.model_validate(body))
        return library_catalog(doc_id)

    @api.post("/api/library/catalogs/{doc_id}/analyses/{analysis_id}/dismiss")
    def analysis_dismiss(doc_id: str, analysis_id: str):
        doc = app.documents.get(doc_id)
        analysis = get_analysis(doc, analysis_id)
        analysis.status = "scartata"
        save_analysis(doc, analysis)
        return analysis_view(analysis)

    # ------------------------------------------------------------------ raggruppamento con Gemini
    group_prompts = PromptStore(app.workspace, prompt="library_group", filename="grouping_prompt.md")
    GROUPING_JOB = "library-grouping"

    def grouping_request(body: dict) -> tuple[list[Document], str, bool]:
        ids = body.get("ids") or []
        if not isinstance(ids, list):
            raise ValueError("Elenco di cataloghi non valido.")
        docs = [app.documents.get(str(i)) for i in dict.fromkeys(ids)]   # senza doppioni, in ordine
        return docs, str(body.get("instructions") or ""), bool(body.get("with_images"))

    def catalog_brief(doc_id: str, projects: list[Project]) -> dict[str, Any]:
        try:
            doc = app.documents.get(doc_id)
        except DocumentError:
            return {"id": doc_id, "title": doc_id, "missing": True}
        card = app.library.card(doc)
        return {"id": doc.id, "title": doc.info.title, "folder": card.folder, "tags": card.tags,
                "page_count": doc.info.page_count, "studied": is_studied(card), "missing": False,
                "projects": [{"id": p.id, "name": p.info.name} for p in projects if doc.id in p.info.documents]}

    def grouping_view(g: Grouping) -> dict[str, Any]:
        projects = app.projects.list()
        names = {p.id: p.info.name for p in projects}
        data = g.model_dump()
        data["catalogs"] = {d: catalog_brief(d, projects) for d in g.documents}
        for group in data["groups"]:
            pid = group["project_id"]
            group["project"] = {"id": pid, "name": names[pid]} if pid in names else None
        return data

    @api.get("/api/library/grouping-prompt")
    def grouping_prompt():
        return {"factory": group_prompts.factory(), "custom": group_prompts.custom(),
                "effective": group_prompts.effective(),
                "max": MAX_CATALOGS, "max_with_images": MAX_CATALOGS_WITH_IMAGES}

    @api.put("/api/library/grouping-prompt")
    def grouping_prompt_save(body: dict = Body(...)):
        group_prompts.save(str(body.get("text") or ""))
        return grouping_prompt()

    @api.post("/api/library/groupings/preview")
    def grouping_preview(body: dict = Body(...)):
        """Prompt esatto, immagini e cataloghi poco studiati, senza chiamare Gemini."""
        docs, instructions, with_images = grouping_request(body)
        prepared = prepare(app, docs, instructions, with_images)
        return {"prompt": prepared.prompt, "catalogs": len(docs), "not_studied": prepared.not_studied,
                "images": [{"id": d, "page": p} for d, p in prepared.images]}

    @api.post("/api/library/groupings")
    def grouping_start(body: dict = Body(...)):
        docs, instructions, with_images = grouping_request(body)
        prepare(app, docs, instructions, with_images)          # errori leggibili prima di partire
        client = app.client()

        def work(job):
            grouping = run_grouping(app, client, docs, instructions, with_images,
                                    lambda message: job.control.progress(0, 0, message))
            return {"grouping_id": grouping.id, "problems": grouping.problems}

        return jobs.submit(GROUPING_JOB, "grouping", f"Gemini confronta {len(docs)} cataloghi", work).to_dict()

    @api.get("/api/library/grouping-job")
    def grouping_job():
        job = jobs.active_for(GROUPING_JOB)
        return job.to_dict() if job else None

    @api.get("/api/library/groupings")
    def groupings():
        return [{"id": g.id, "created_at": g.created_at, "status": g.status, "catalogs": len(g.documents),
                 "groups": len(g.groups), "ungrouped": len(g.ungrouped),
                 "projects": sum(1 for x in g.groups if x.project_id)} for g in list_groupings(app.workspace)]

    @api.get("/api/library/groupings/{grouping_id}")
    def grouping_get(grouping_id: str):
        return grouping_view(get_grouping(app.workspace, grouping_id))

    @api.post("/api/library/groupings/{grouping_id}/move")
    def grouping_move(grouping_id: str, body: dict = Body(...)):
        g = get_grouping(app.workspace, grouping_id)
        move_document(g, str(body.get("document") or ""), str(body.get("to") or ""))
        save_grouping(app.workspace, g)
        return grouping_view(g)

    @api.put("/api/library/groupings/{grouping_id}/groups/{index}")
    def grouping_update(grouping_id: str, index: int, body: dict = Body(...)):
        g = get_grouping(app.workspace, grouping_id)
        update_group(g, index, name=body.get("name"),
                     tags=[str(t) for t in body["tags"]] if isinstance(body.get("tags"), list) else None)
        save_grouping(app.workspace, g)
        return grouping_view(g)

    @api.post("/api/library/groupings/{grouping_id}/groups/{index}/tags")
    def grouping_tags(grouping_id: str, index: int, body: dict = Body(...)):
        g = get_grouping(app.workspace, grouping_id)
        apply_group_tags(app, g, index, [str(t) for t in body.get("tags") or []])
        save_grouping(app.workspace, g)
        return grouping_view(g)

    @api.post("/api/library/groupings/{grouping_id}/groups/{index}/project")
    def grouping_project(grouping_id: str, index: int, body: dict = Body(...)):
        g = get_grouping(app.workspace, grouping_id)
        create_group_project(app, g, index, str(body.get("name") or ""))
        save_grouping(app.workspace, g)
        return grouping_view(g)

    @api.delete("/api/library/catalogs/{doc_id}")
    def library_delete(doc_id: str):
        doc = app.documents.get(doc_id)
        app.delete_document(doc)
        return {"deleted": doc.id}

    @api.post("/api/library/catalogs/{doc_id}/collections")
    def collection_add(doc_id: str, body: dict = Body(...)):
        doc = app.documents.get(doc_id)
        app.library.add_collection(doc, int(body["start"]), int(body["end"]), str(body.get("tag", "")),
                                   str(body.get("comment") or ""))
        return library_catalog(doc_id)

    @api.put("/api/library/catalogs/{doc_id}/collections/{coll_id}")
    def collection_update(doc_id: str, coll_id: str, body: dict = Body(...)):
        doc = app.documents.get(doc_id)
        app.library.update_collection(doc, coll_id, int(body["start"]), int(body["end"]),
                                      str(body.get("tag", "")), str(body.get("comment") or ""))
        return library_catalog(doc_id)

    @api.delete("/api/library/catalogs/{doc_id}/collections/{coll_id}")
    def collection_delete(doc_id: str, coll_id: str):
        app.library.remove_collection(app.documents.get(doc_id), coll_id)
        return library_catalog(doc_id)

    @api.post("/api/library/move")
    def library_move(body: dict = Body(...)):
        return {"folder": app.library.move(docs_from_body(body), str(body.get("folder") or ""))}

    @api.post("/api/library/tags")
    def library_tags(body: dict = Body(...)):
        app.library.retag(docs_from_body(body), add=body.get("add") or [], remove=body.get("remove") or [])
        return {"ok": True}

    @api.post("/api/library/folders")
    def folder_create(body: dict = Body(...)):
        return {"folder": app.library.create_folder(str(body.get("path") or ""))}

    @api.put("/api/library/folders")
    def folder_rename(body: dict = Body(...)):
        return {"folder": app.library.rename_folder(str(body.get("path") or ""), str(body.get("new_path") or ""))}

    @api.delete("/api/library/folders")
    def folder_delete(path: str):
        app.library.delete_folder(path)
        return {"deleted": path}

    @api.post("/api/library/projects")
    def project_from_library(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("Dai un nome al progetto.")
        return project_summary(app.create_project_from_library(name, docs_from_body(body)))

    @api.post("/api/projects/{pid}/documents/from-library")
    def project_add_from_library(pid: str, body: dict = Body(...)):
        project = project_or_404(pid)
        no_job(project)
        added = []
        for doc in docs_from_body(body):
            if doc.id not in project.info.documents:
                app.projects.add_document(project, doc.id)
                pages = app.library.card(doc).pages_with_tag("dati")
                if pages:
                    project.set_settings(doc.id, DocumentSettings(pages=format_pages(pages)))
                    project.save()
                added.append(doc.id)
        return {"added": added}

    # ------------------------------------------------------------------ pagine dei PDF
    @api.get("/api/documents/{doc_id}/thumb/{page}")
    def thumbnail(doc_id: str, page: int):
        doc = app.documents.get(doc_id)
        path = render_thumbnail(doc.pdf_path, page, doc.folder / "thumbs" / f"page-{page:04d}.png")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @api.get("/api/documents/{doc_id}/page/{page}")
    def page_image(doc_id: str, page: int):
        doc = app.documents.get(doc_id)
        if not 1 <= page <= doc.info.page_count:
            raise HTTPException(404, "Pagina inesistente.")
        images = render_pages(doc.pdf_path, doc.pages_dir, app.config.render.dpi, [page], show_progress=False)
        return FileResponse(images[page], media_type="image/png")

    @api.get("/api/documents/{doc_id}/pdf")
    def document_pdf(doc_id: str):
        doc = app.documents.get(doc_id)
        return FileResponse(doc.pdf_path, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{doc.info.source_filename}"'})

    @api.post("/api/references")
    def upload_reference(file: UploadFile = File(...)):
        name = Path(file.filename or "riferimento.csv").name
        if not name.lower().endswith((".csv", ".txt")):
            raise HTTPException(400, "Il riferimento deve essere un file CSV.")
        folder = app.workspace.root / "references"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        with open(target, "wb") as out:
            shutil.copyfileobj(file.file, out)
        columns, rows = read_csv_rows(target)
        return {"path": str(target), "name": name, "columns": columns, "rows": len(rows)}

    # ------------------------------------------------------------------ lavori
    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Operazione non trovata.")
        data = job.to_dict()
        data["usage"] = usage()
        # messaggi di servizio arrivati durante il lavoro (sovraccarico, quote, ...)
        data["events"] = [{"time": time.strftime("%H:%M:%S", time.localtime(e["ts"])), "text": e["text"]}
                          for e in app.events_since(job.started_ts)]
        return data

    @api.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(404, "Operazione non trovata.")
        return job.to_dict()

    @api.get("/api/projects/{pid}/job")
    def project_job(pid: str):
        job = jobs.latest_for(pid)
        return job.to_dict() if job else None

    # ------------------------------------------------------------------ configurazione guidata
    def session_view(session: SetupSession) -> dict[str, Any]:
        st = session.state
        docs = session.documents
        view: dict[str, Any] = {
            "state": {
                "context_pages": [list(r) for r in st.context_pages],
                "context_pages_text": format_page_refs(st.context_pages, docs),
                "preview_pages": [list(r) for r in st.preview_pages],
                "preview_pages_text": format_page_refs(st.preview_pages, docs),
                "goal": st.goal,
                "reference": Path(st.reference_csv).name if st.reference_csv else None,
                "current_round": st.current_round,
                "last_round": st.last_round,
                "saved_at": st.saved_at,
                "primer_name": st.primer_name,
                "spec_name": st.spec_name,
            },
            "rounds": session.rounds(),
            "round": None,
        }
        if st.current_round == 0:
            return view
        notes = session.round_notes()
        spec_info: dict[str, Any] = {"valid": True, "error": None}
        try:
            spec = session.load_spec()
            spec_info.update(
                row=spec.row.strip(),
                pages=spec.pages,
                columns=[{"name": c.name, "description": c.description.strip(), "required": c.required,
                          "context": c.context} for c in spec.columns],
                output_columns=spec.output_columns,
                page_column=spec.output.page_column,
                source_column=spec.output.source_column,
            )
        except SpecError as e:
            spec_info.update(valid=False, error=str(e))
        preview = None
        info = notes.get("preview")
        if info and session.preview_csv_path.exists():
            columns, rows = read_csv_rows(session.preview_csv_path)
            preview = {
                "rows_per_page": info.get("rows_per_page") or [],
                "failed": info.get("failed") or {},
                "columns": columns,
                "rows": rows[:MAX_ROWS_IN_VIEW],
                "total_rows": len(rows),
                "anomalies": info.get("anomalies") or [],
                "comparison": info.get("comparison"),
                "comparison_note": info.get("comparison_note") or "",
            }
        view["round"] = {
            "number": st.current_round,
            "origin": notes.get("origin"),
            "feedback": notes.get("feedback"),
            "summary": notes.get("summary", ""),
            "questions": notes.get("questions") or [],
            "primer": session.primer_text(),
            "spec_text": session.spec_text(),
            "spec": spec_info,
            "document_notes": session.document_notes(),
            "preview": preview,
            "columns_check": (compare_columns(session.project.info.column_names,
                                              [c["name"] for c in spec_info.get("columns", [])])
                              if session.project.info.fields and spec_info["valid"] else None),
        }
        return view

    @api.get("/api/projects/{pid}/setup")
    def get_setup(pid: str):
        project = project_or_404(pid)
        session = SetupSession.load(app, project)
        docs = app.project_documents(project)
        # le pagine d'esempio e i campi segnati nella biblioteca hanno la precedenza
        examples = app.example_page_refs(docs, MAX_SELECTED_PAGES)
        return {
            "session": session_view(session) if session else None,
            "suggested_pages": [list(r) for r in (examples or default_context_refs(docs))],
            "table": project.info.has_table,
        }

    @api.post("/api/projects/{pid}/setup")
    def new_setup(pid: str, body: dict = Body(...)):
        """Crea una nuova sessione (sostituisce quella esistente). Non chiama Gemini."""
        project = project_or_404(pid)
        no_job(project)
        docs = app.project_documents(project)
        refs = refs_from_body(body.get("context_pages"), docs)
        from_primer, from_spec = body.get("from_primer"), body.get("from_spec")
        if from_primer or from_spec:
            if not (from_primer and from_spec):
                raise SetupError("Per partire da file esistenti scegli sia il primer sia la specifica.")
            refs = refs or default_context_refs(docs)
        name = slugify(body["name"]) if body.get("name") else None
        session = SetupSession.new(
            app, project, refs,
            goal=str(body.get("goal") or ""),
            reference_csv=Path(body["reference"]) if body.get("reference") else None,
            primer_name=name or from_primer, spec_name=name or from_spec,
        )
        if from_primer:
            session.start_from_existing(from_primer, from_spec)
        return session_view(session)

    @api.get("/api/projects/{pid}/setup/prompt")
    def setup_prompt(pid: str, kind: str = "initial", feedback: str = ""):
        session = session_or_404(project_or_404(pid))
        if kind == "revise":
            prompt = session.build_revise_prompt(feedback or "(la tua correzione)")
        else:
            prompt = session.build_initial_prompt()
        return {"prompt": prompt, "attachment": session.attachment_description()}

    def _setup_job(project: Project, session: SetupSession, kind: str, label: str, step) -> dict[str, Any]:
        def work(job):
            if step is not None:
                job.message = label
                step()
            job.message = "Prova di estrazione"
            preview = session.run_preview(show_progress=False, control=job.control)
            return {"rows": preview.index.stats.get("righe_finali", 0), "round": session.state.current_round}

        return jobs.submit(project.id, kind, label, work).to_dict()

    @api.post("/api/projects/{pid}/setup/start")
    def setup_start(pid: str):
        project = project_or_404(pid)
        session = session_or_404(project)
        if session.state.current_round:
            raise SetupError("La sessione è già avviata: usa le correzioni.")
        return _setup_job(project, session, "setup", "Gemini sta analizzando le pagine scelte", session.start)

    @api.post("/api/projects/{pid}/setup/revise")
    def setup_revise(pid: str, body: dict = Body(...)):
        project = project_or_404(pid)
        session = session_or_404(project)
        feedback = str(body.get("feedback") or "").strip()
        if not feedback:
            raise SetupError("Scrivi cosa vuoi correggere.")
        return _setup_job(project, session, "revise", "Gemini sta applicando la correzione",
                          lambda: session.revise(feedback))

    @api.post("/api/projects/{pid}/setup/preview")
    def setup_preview(pid: str, body: dict = Body(default={})):
        project = project_or_404(pid)
        session = session_or_404(project)
        if body.get("pages"):
            session.set_preview_pages(refs_from_body(body["pages"], session.documents))
        return _setup_job(project, session, "preview", "Prova di estrazione", None)

    @api.post("/api/projects/{pid}/setup/context-pages")
    def setup_context_pages(pid: str, body: dict = Body(...)):
        session = session_or_404(project_or_404(pid))
        session.set_context_pages(refs_from_body(body.get("pages"), session.documents))
        return session_view(session)

    @api.post("/api/projects/{pid}/setup/round/{number}")
    def setup_go_to(pid: str, number: int):
        session = session_or_404(project_or_404(pid))
        session.go_to(number)
        return session_view(session)

    @api.put("/api/projects/{pid}/setup/primer")
    def setup_edit_primer(pid: str, body: dict = Body(...)):
        session = session_or_404(project_or_404(pid))
        session.manual_edit_done(primer_text=str(body.get("text") or ""))
        return session_view(session)

    @api.put("/api/projects/{pid}/setup/spec")
    def setup_edit_spec(pid: str, body: dict = Body(...)):
        session = session_or_404(project_or_404(pid))
        session.manual_edit_done(spec_text=str(body.get("text") or ""))
        return session_view(session)

    @api.put("/api/projects/{pid}/setup/notes")
    def setup_edit_notes(pid: str, body: dict = Body(...)):
        session = session_or_404(project_or_404(pid))
        ids = {d.id for d in session.documents}
        notes = {str(k): str(v).strip() for k, v in (body.get("notes") or {}).items() if k in ids and str(v).strip()}
        session.manual_edit_done(document_notes=notes)
        return session_view(session)

    @api.post("/api/projects/{pid}/setup/save")
    def setup_save(pid: str, body: dict = Body(default={})):
        session = session_or_404(project_or_404(pid))
        if not session.state.current_round:
            raise SetupError("Non c'è ancora niente da salvare.")
        if body.get("name"):
            session.state.primer_name = session.state.spec_name = slugify(str(body["name"]))
            session.save_state()
        conflicts = session.files_that_would_change()
        if conflicts and not body.get("overwrite") and not session.state.saved_at:
            return _error(409, "Esistono già file con questo nome.", conflicts=[p.name for p in conflicts],
                          name=session.state.spec_name)
        primer_path, spec_path = session.save()
        reused = session.reuse_preview()
        return {"primer": str(primer_path), "spec": str(spec_path), "reused_pages": reused,
                "spec_name": session.state.spec_name}

    @api.get("/api/projects/{pid}/setup/preview.csv")
    def setup_preview_csv(pid: str):
        session = session_or_404(project_or_404(pid))
        if not session.preview_csv_path.exists():
            raise HTTPException(404, "Nessuna anteprima.")
        return FileResponse(session.preview_csv_path, filename=f"{pid}_anteprima.csv", media_type="text/csv")

    # ------------------------------------------------------------------ indicizzazione
    def index_plan_for(project: Project) -> dict[str, Any]:
        base = app.project_spec(project)
        primer = app.project_primer(project)
        extractor = Extractor(None, app.config)  # type: ignore[arg-type]  (solo impronte)
        rows = []
        for doc in app.project_documents(project):
            spec = app.document_spec(project, doc, base)
            pages = selected_pages(doc, spec)
            manifest_path = doc.extraction_dir(spec.name) / "manifest.json"
            done: set[int] = set()
            failed = 0
            changed = False
            if manifest_path.exists():
                m = Manifest.load_or_create(manifest_path, doc.info.page_count)
                done = {int(k) for k, v in m.data["pages"].items() if v["status"] == DONE}
                failed = len(m.failed_pages())
                changed = bool(done) and m.meta.get("fingerprint") != extractor.fingerprint(spec, primer)
            todo = [p for p in pages if changed or p not in done]
            rows.append({
                "id": doc.id, "title": doc.info.title, "pages": len(pages),
                "pages_text": spec.pages or f"1-{doc.info.page_count}",
                "done": len([p for p in pages if p in done]) if not changed else 0,
                "todo": len(todo), "failed": failed, "spec_changed": changed,
                "has_notes": bool(project.settings_for(doc.id).notes.strip()),
            })
        todo = sum(r["todo"] for r in rows)
        keys = max(1, len(load_api_keys()))
        rpm = app.config.gemini.max_requests_per_minute_total or app.config.gemini.requests_per_minute_per_key * keys
        return {
            "spec": base.name, "primer": project.info.primer, "documents": rows,
            "pages": sum(r["pages"] for r in rows), "done": sum(r["done"] for r in rows), "todo": todo,
            "spec_changed": [r["id"] for r in rows if r["spec_changed"]],
            "estimate_minutes": max(1, round(todo / rpm)) if todo else 0,
        }

    @api.get("/api/projects/{pid}/index/plan")
    def index_plan(pid: str):
        return index_plan_for(project_or_404(pid))

    @api.post("/api/projects/{pid}/index")
    def start_index(pid: str, body: dict = Body(default={})):
        project = project_or_404(pid)
        base = app.project_spec(project)
        primer = app.project_primer(project)
        force = bool(body.get("force"))
        extractor = app.extractor()
        docs = app.project_documents(project)
        specs = {d.id: app.document_spec(project, d, base) for d in docs}
        # prima dell'avvio: se le regole di un PDF sono cambiate serve il consenso esplicito
        # (force), che rifà da capo SOLO quei PDF
        for doc in docs:
            try:
                extractor.open_manifest(doc, specs[doc.id], primer)
            except SpecChangedError:
                if not force:
                    raise
                extractor.open_manifest(doc, specs[doc.id], primer, force=True)
        plan = index_plan_for(project)
        total = plan["todo"]

        def work(job):
            done_before = 0
            reports = {}
            for doc in docs:
                if job.control.cancel.is_set():
                    break
                spec = specs[doc.id]

                def progress(done: int, _total: int, _msg: str, offset=done_before, title=doc.info.title) -> None:
                    job.done, job.total = offset + done, total
                    job.message = f"Indicizzazione di {title}"

                control = RunControl(progress=progress, cancel=job.control.cancel)
                report = extractor.run(doc, spec, primer, show_progress=False, control=control)
                reports[doc.id] = {
                    "succeeded": report.succeeded,
                    "failed": {str(k): v for k, v in report.failed.items()},
                    "not_attempted": report.not_attempted,
                    "stopped_reason": report.stopped_reason,
                }
                done_before += report.requested
                if report.stopped_reason and not job.control.cancel.is_set():
                    break      # quota esaurita o troppi errori: inutile continuare con gli altri PDF
            result = app.export_project(project)
            if not project.info.spec:
                app.assign_project_rules(project, project.info.primer or "", base.name)
            stopped = next((r["stopped_reason"] for r in reports.values() if r["stopped_reason"]), None)
            return {"documents": reports, "stats": result.index.stats, "stopped_reason": stopped}

        return jobs.submit(project.id, "index", f"Indicizzazione del progetto '{project.info.name}'", work).to_dict()

    @api.get("/api/projects/{pid}/results")
    def results(pid: str):
        project = project_or_404(pid)
        app.project_spec(project)
        result = app.export_project(project)
        if not result.documents:
            return {"available": False}
        level_order = {"errore": 0, "avviso": 1}
        anomalies = []
        for doc, r in result.documents:
            for a in r.index.anomalies:
                anomalies.append({"document": doc.id, "file": doc.info.source_filename, "level": a.level,
                                  "page": a.page, "row": a.csv_row, "column": a.column, "value": a.value,
                                  "problem": a.problem})
        anomalies.sort(key=lambda a: (level_order.get(a["level"], 2), a["file"], a["page"] or 0, a["row"] or 0))
        levels = {lvl: sum(1 for a in anomalies if a["level"] == lvl) for lvl in ("errore", "avviso", "info")}
        spec = app.project_spec(project)
        return {
            "available": True,
            "stats": result.index.stats,
            "documents": [{"id": d.id, "title": d.info.title, "file": d.info.source_filename,
                           "rows": r.index.stats.get("righe_finali", 0), "errors": r.index.stats.get("errori", 0),
                           "warnings": r.index.stats.get("avvisi", 0), "stale": r.stale,
                           "csv": r.csv_path.name} for d, r in result.documents],
            "not_indexed": [d.id for d in result.not_indexed],
            "stale": [d.id for d in result.stale],
            "columns": result.index.columns,
            "page_column": spec.output.page_column,
            "source_column": spec.output.source_column,
            "rows": result.index.rows[:MAX_ROWS_IN_VIEW],
            "anomalies": anomalies[:MAX_ROWS_IN_VIEW],
            "anomalies_total": len(anomalies),
            "anomalies_by_level": levels,
            "csv": result.csv_path.name,
        }

    @api.get("/api/projects/{pid}/download/{kind}")
    def download(pid: str, kind: str, doc: str | None = None):
        project = project_or_404(pid)
        out_dir = app.project_export_dir(project)
        if kind == "csv":
            path = out_dir / f"{project.id}.csv"
        elif kind == "anomalie":
            path = out_dir / f"{project.id}_anomalie.csv"
        elif kind == "documento" and doc:
            path = out_dir / "documenti" / f"{doc_of(project, doc).id}.csv"
        else:
            raise HTTPException(404, "File sconosciuto.")
        if not path.exists():
            raise HTTPException(404, "File non ancora prodotto: esegui l'indicizzazione o apri i risultati.")
        return FileResponse(path, filename=path.name, media_type="text/csv")

    @api.get("/api/projects/{pid}/search", response_class=HTMLResponse)
    def search_page(pid: str):
        from ragpdf.indexing.search_page import build_search_page

        project = project_or_404(pid)
        result = app.export_project(project)
        path = build_search_page(app, project, result, pdf_link="/api/documents/{doc}/pdf#page={page}")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ pagina
    @api.get("/")
    def index_html():
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    api.mount("/static", _UiFiles(directory=STATIC_DIR), name="static")
    return api


def serve(app: App, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Interfaccia disponibile su {url}  (chiudi con Ctrl+C)")
    uvicorn.run(create_app(app), host=host, port=port, log_level="warning")
