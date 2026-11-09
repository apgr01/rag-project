"""Server locale dell'interfaccia grafica: ``ragpdf ui``.

Espone delle API JSON sopra la stessa logica usata dalla riga di comando (``App``,
``SetupSession``, ``Extractor``) e serve la pagina web in ``static/``. Ascolta solo su
127.0.0.1: è raggiungibile solo da questo computer, e le chiavi API restano nel file .env.

Regole dell'interfaccia, garantite anche qui:

- niente parte verso Gemini se non da un endpoint esplicito (``.../start``,
  ``.../revise``, ``.../preview``, ``.../index``), che la pagina chiama solo dopo una
  conferma dell'utente;
- le operazioni lunghe sono *lavori* in background (vedi ``jobs.py``), uno per documento;
- i prompt si possono vedere prima dell'invio (``.../prompt``).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ragpdf import __version__
from ragpdf.app import App, PrimerMissingError
from ragpdf.core.documents import Document, DocumentError, DuplicateDocumentError
from ragpdf.core.gemini import GeminiError, load_api_keys
from ragpdf.core.manifest import DONE, FAILED, Manifest
from ragpdf.core.pdf_tools import has_text_layer, render_thumbnail
from ragpdf.core.render import format_pages, parse_page_ranges, render_pages
from ragpdf.core.workspace import slugify
from ragpdf.indexing.extractor import Extractor, SpecChangedError, selected_pages
from ragpdf.indexing.spec import SpecError, read_csv_rows
from ragpdf.setup_session import SetupError, SetupSession, default_context_pages
from ragpdf.web.jobs import JobConflictError, JobManager

STATIC_DIR = Path(__file__).parent / "static"
MAX_ROWS_IN_VIEW = 300


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
    def doc_or_404(doc_id: str) -> Document:
        return app.documents.get(doc_id)

    def session_or_404(doc: Document) -> SetupSession:
        session = SetupSession.load(app, doc)
        if session is None:
            raise HTTPException(404, "Nessuna configurazione guidata per questo documento.")
        return session

    def usage() -> dict[str, Any] | None:
        u = getattr(app._client, "usage", None)
        if u is None:
            return None
        return {"requests": u.requests, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "text": u.describe()}

    def document_summary(doc: Document) -> dict[str, Any]:
        indexing = []
        for spec_name in doc.list_extractions():
            m = Manifest.load_or_create(doc.extraction_dir(spec_name) / "manifest.json", doc.info.page_count)
            c = m.counts()
            indexing.append({"spec": spec_name, "done": c.get(DONE, 0), "failed": c.get(FAILED, 0)})
        session = SetupSession.load(app, doc)
        job = jobs.active_for(doc.id)
        return {
            **doc.info.model_dump(),
            "indexing": indexing,
            "setup": None if session is None else {
                "round": session.state.current_round,
                "saved": bool(session.state.saved_at),
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

    # ------------------------------------------------------------------ documenti
    @api.get("/api/documents")
    def list_documents():
        return [document_summary(d) for d in app.documents.list()]

    @api.post("/api/documents")
    def upload_document(file: UploadFile = File(...)):
        name = Path(file.filename or "documento.pdf").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "Il file deve essere un PDF.")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / name
            with open(target, "wb") as out:
                shutil.copyfileobj(file.file, out)
            doc = app.documents.add(target)
        result = document_summary(doc)
        result["scanned"] = not has_text_layer(doc.pdf_path)
        return result

    @api.get("/api/documents/{doc_id}")
    def get_document(doc_id: str):
        return document_summary(doc_or_404(doc_id))

    @api.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: str):
        doc = doc_or_404(doc_id)
        if jobs.active_for(doc.id):
            raise HTTPException(409, "C'è un'operazione in corso su questo documento.")
        app.documents.remove(doc)
        return {"deleted": doc.id}

    @api.get("/api/documents/{doc_id}/thumb/{page}")
    def thumbnail(doc_id: str, page: int):
        doc = doc_or_404(doc_id)
        path = render_thumbnail(doc.pdf_path, page, doc.folder / "thumbs" / f"page-{page:04d}.png")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @api.get("/api/documents/{doc_id}/page/{page}")
    def page_image(doc_id: str, page: int):
        doc = doc_or_404(doc_id)
        if not 1 <= page <= doc.info.page_count:
            raise HTTPException(404, "Pagina inesistente.")
        images = render_pages(doc.pdf_path, doc.pages_dir, app.config.render.dpi, [page], show_progress=False)
        return FileResponse(images[page], media_type="image/png")

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
        return data

    @api.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(404, "Operazione non trovata.")
        return job.to_dict()

    @api.get("/api/documents/{doc_id}/job")
    def document_job(doc_id: str):
        job = jobs.latest_for(doc_id)
        return job.to_dict() if job else None

    # ------------------------------------------------------------------ configurazione guidata
    def session_view(session: SetupSession) -> dict[str, Any]:
        st = session.state
        view: dict[str, Any] = {
            "state": {
                "context_pages": st.context_pages,
                "context_pages_text": format_pages(st.context_pages),
                "preview_pages": st.preview_pages,
                "preview_pages_text": format_pages(st.preview_pages),
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
                "pages": info["pages"],
                "rows_per_page": info["rows_per_page"],
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
            "preview": preview,
        }
        return view

    @api.get("/api/documents/{doc_id}/setup")
    def get_setup(doc_id: str):
        doc = doc_or_404(doc_id)
        session = SetupSession.load(app, doc)
        return {
            "session": session_view(session) if session else None,
            "suggested_pages": format_pages(default_context_pages(doc.info.page_count)),
        }

    @api.post("/api/documents/{doc_id}/setup")
    def new_setup(doc_id: str, body: dict = Body(...)):
        """Crea una nuova sessione (sostituisce quella esistente). Non chiama Gemini."""
        doc = doc_or_404(doc_id)
        if jobs.active_for(doc.id):
            raise HTTPException(409, "C'è un'operazione in corso su questo documento.")
        pages = parse_page_ranges(str(body.get("context_pages") or ""), doc.info.page_count) or []
        from_primer, from_spec = body.get("from_primer"), body.get("from_spec")
        if from_primer or from_spec:
            if not (from_primer and from_spec):
                raise SetupError("Per partire da file esistenti scegli sia il primer sia la specifica.")
            pages = pages or default_context_pages(doc.info.page_count)
        name = slugify(body["name"]) if body.get("name") else None
        session = SetupSession.new(
            app, doc, pages,
            goal=str(body.get("goal") or ""),
            reference_csv=Path(body["reference"]) if body.get("reference") else None,
            primer_name=name or from_primer, spec_name=name or from_spec,
        )
        if from_primer:
            session.start_from_existing(from_primer, from_spec)
        return session_view(session)

    @api.get("/api/documents/{doc_id}/setup/prompt")
    def setup_prompt(doc_id: str, kind: str = "initial", feedback: str = ""):
        session = session_or_404(doc_or_404(doc_id))
        if kind == "revise":
            prompt = session.build_revise_prompt(feedback or "(la tua correzione)")
        else:
            prompt = session.build_initial_prompt()
        return {
            "prompt": prompt,
            "attachment": f"estratto PDF di {len(session.state.context_pages)} pagine "
                          f"({format_pages(session.state.context_pages)})",
        }

    def _start_job(doc: Document, session: SetupSession, kind: str, label: str, step) -> dict[str, Any]:
        def work(job):
            if step is not None:
                job.message = label
                step()
            job.message = "Prova di estrazione"
            preview = session.run_preview(show_progress=False, control=job.control)
            return {"rows": preview.index.stats.get("righe_finali", 0), "round": session.state.current_round}

        return jobs.submit(doc.id, kind, label, work).to_dict()

    @api.post("/api/documents/{doc_id}/setup/start")
    def setup_start(doc_id: str):
        doc = doc_or_404(doc_id)
        session = session_or_404(doc)
        if session.state.current_round:
            raise SetupError("La sessione è già avviata: usa le correzioni.")
        return _start_job(doc, session, "setup", "Gemini sta analizzando le pagine scelte", session.start)

    @api.post("/api/documents/{doc_id}/setup/revise")
    def setup_revise(doc_id: str, body: dict = Body(...)):
        doc = doc_or_404(doc_id)
        session = session_or_404(doc)
        feedback = str(body.get("feedback") or "").strip()
        if not feedback:
            raise SetupError("Scrivi cosa vuoi correggere.")
        return _start_job(doc, session, "revise", "Gemini sta applicando la correzione",
                          lambda: session.revise(feedback))

    @api.post("/api/documents/{doc_id}/setup/preview")
    def setup_preview(doc_id: str, body: dict = Body(default={})):
        doc = doc_or_404(doc_id)
        session = session_or_404(doc)
        if body.get("pages"):
            session.set_preview_pages(parse_page_ranges(str(body["pages"]), doc.info.page_count) or [])
        return _start_job(doc, session, "preview", "Prova di estrazione", None)

    @api.post("/api/documents/{doc_id}/setup/context-pages")
    def setup_context_pages(doc_id: str, body: dict = Body(...)):
        doc = doc_or_404(doc_id)
        session = session_or_404(doc)
        session.set_context_pages(parse_page_ranges(str(body.get("pages") or ""), doc.info.page_count) or [])
        return session_view(session)

    @api.post("/api/documents/{doc_id}/setup/round/{number}")
    def setup_go_to(doc_id: str, number: int):
        session = session_or_404(doc_or_404(doc_id))
        session.go_to(number)
        return session_view(session)

    @api.put("/api/documents/{doc_id}/setup/primer")
    def setup_edit_primer(doc_id: str, body: dict = Body(...)):
        session = session_or_404(doc_or_404(doc_id))
        session.manual_edit_done(primer_text=str(body.get("text") or ""))
        return session_view(session)

    @api.put("/api/documents/{doc_id}/setup/spec")
    def setup_edit_spec(doc_id: str, body: dict = Body(...)):
        session = session_or_404(doc_or_404(doc_id))
        session.manual_edit_done(spec_text=str(body.get("text") or ""))
        return session_view(session)

    @api.post("/api/documents/{doc_id}/setup/save")
    def setup_save(doc_id: str, body: dict = Body(default={})):
        session = session_or_404(doc_or_404(doc_id))
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

    @api.get("/api/documents/{doc_id}/setup/preview.csv")
    def setup_preview_csv(doc_id: str):
        session = session_or_404(doc_or_404(doc_id))
        if not session.preview_csv_path.exists():
            raise HTTPException(404, "Nessuna anteprima.")
        return FileResponse(session.preview_csv_path, filename=f"{doc_id}_anteprima.csv", media_type="text/csv")

    # ------------------------------------------------------------------ indicizzazione
    def spec_for(doc: Document, given: str | None):
        name = given or doc.info.spec
        if not name:
            raise SpecError("Questo documento non ha ancora una specifica: completa prima la configurazione.")
        return app.load_spec(name)

    @api.get("/api/documents/{doc_id}/index/plan")
    def index_plan(doc_id: str, spec: str | None = None):
        doc = doc_or_404(doc_id)
        spec_obj = spec_for(doc, spec)
        primer = app.primer_text(doc) if doc.info.primer else ""
        extractor = Extractor(None, app.config)  # type: ignore[arg-type]
        pages = selected_pages(doc, spec_obj)
        manifest_path = doc.extraction_dir(spec_obj.name) / "manifest.json"
        done, failed, changed = set(), {}, False
        if manifest_path.exists():
            m = Manifest.load_or_create(manifest_path, doc.info.page_count)
            done = {int(k) for k, v in m.data["pages"].items() if v["status"] == DONE}
            failed = m.failed_pages()
            changed = bool(done) and m.meta.get("fingerprint") != extractor.fingerprint(spec_obj, primer)
        todo = [p for p in pages if changed or p not in done]
        keys = max(1, len(load_api_keys()))
        rpm = app.config.gemini.max_requests_per_minute_total or app.config.gemini.requests_per_minute_per_key * keys
        return {
            "spec": spec_obj.name,
            "primer": doc.info.primer,
            "pages": len(pages),
            "pages_text": spec_obj.pages or f"1-{doc.info.page_count}",
            "done": len([p for p in pages if p in done]),
            "failed": len(failed),
            "todo": len(todo),
            "spec_changed": changed,
            "estimate_minutes": max(1, round(len(todo) / rpm)) if todo else 0,
        }

    @api.post("/api/documents/{doc_id}/index")
    def start_index(doc_id: str, body: dict = Body(default={})):
        doc = doc_or_404(doc_id)
        spec = spec_for(doc, body.get("spec"))
        primer = app.primer_text(doc) if doc.info.primer else ""
        force = bool(body.get("force"))
        extractor = app.extractor()
        extractor.open_manifest(doc, spec, primer, force=force)  # se le regole sono cambiate: 409

        def work(job):
            job.message = "Indicizzazione"
            report = extractor.run(doc, spec, primer, show_progress=False, control=job.control)
            result = app.export(doc, spec)
            if not doc.info.spec:
                app.assign_spec(doc, spec.name)
            return {
                "succeeded": report.succeeded,
                "failed": {str(k): v for k, v in report.failed.items()},
                "not_attempted": report.not_attempted,
                "stopped_reason": report.stopped_reason,
                "stats": result.index.stats,
            }

        return jobs.submit(doc.id, "index", f"Indicizzazione con la specifica '{spec.name}'", work).to_dict()

    @api.get("/api/documents/{doc_id}/results")
    def results(doc_id: str, spec: str | None = None):
        doc = doc_or_404(doc_id)
        spec_obj = spec_for(doc, spec)
        if not (doc.extraction_dir(spec_obj.name) / "manifest.json").exists():
            return {"available": False, "spec": spec_obj.name}
        result = app.export(doc, spec_obj)
        anomalies = [
            {"level": a.level, "page": a.page, "row": a.csv_row, "column": a.column, "value": a.value,
             "problem": a.problem}
            # prima gli errori, poi gli avvisi, infine le note informative
            for a in sorted(result.index.anomalies,
                            key=lambda a: ({"errore": 0, "avviso": 1}.get(a.level, 2), a.page or 0, a.csv_row or 0))
        ]
        levels = {lvl: sum(1 for a in anomalies if a["level"] == lvl) for lvl in ("errore", "avviso", "info")}
        return {
            "available": True,
            "spec": spec_obj.name,
            "stats": result.index.stats,
            "stale": result.stale,
            "columns": result.index.columns,
            "rows": result.index.rows[:MAX_ROWS_IN_VIEW],
            "anomalies": anomalies[:MAX_ROWS_IN_VIEW],
            "anomalies_total": len(anomalies),
            "anomalies_by_level": levels,
            "csv": result.csv_path.name,
        }

    @api.get("/api/documents/{doc_id}/download/{kind}")
    def download(doc_id: str, kind: str, spec: str | None = None):
        doc = doc_or_404(doc_id)
        spec_obj = spec_for(doc, spec)
        csv_path = app.workspace.exports_dir / spec_obj.name / f"{doc.id}.csv"
        path = csv_path if kind == "csv" else csv_path.with_name(f"{csv_path.stem}_anomalie.csv")
        if not path.exists():
            raise HTTPException(404, "File non ancora prodotto: esegui l'indicizzazione o rigenera i risultati.")
        return FileResponse(path, filename=path.name, media_type="text/csv")

    # ------------------------------------------------------------------ pagina
    @api.get("/")
    def index_html():
        return FileResponse(STATIC_DIR / "index.html")

    api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
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
