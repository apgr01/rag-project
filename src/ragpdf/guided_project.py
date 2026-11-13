"""Progetto guidato: dalla scheda di un PDF a un progetto, con l'aiuto di Gemini.

L'utente parte da un catalogo e descrive l'**indice che vuole** (cosa è una riga, un elenco di
colonne, un CSV d'esempio, oppure a parole). Gemini riceve la richiesta, la scheda del catalogo
di partenza e le schede dei **cataloghi elaborati** della biblioteca (con descrizione o dati
presenti), e risponde con:

- la **tabella** ripulita (descrizione, riga, colonne): diventerà l'impostazione globale del
  progetto;
- per il catalogo di partenza: quali colonne copre e quali sezioni contengono i dati;
- i **candidati** (*compatibile* / *parziale*) con motivo, colonne coperte e sezioni con i dati.

Come per l'analisi, il prompt ha le **indicazioni** modificabili e una **cornice** fissa con il
formato della risposta, che prevale. Cataloghi e sezioni hanno codici brevi (``C3``, ``C3/2``,
``P/1``) che nello schema sono degli ``enum``; la risposta è poi convalidata in modo rigoroso.
Il risultato è un **report** in ``data/library/guided/<id>.json``: l'utente sceglie i
cataloghi e le sezioni e crea il progetto.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ragpdf.core.documents import Document
from ragpdf.core.fileio import utc_now, write_json
from ragpdf.core.gemini import LLMClient
from ragpdf.core.library import PAGE_TAGS, LibraryCard, LibraryError, normalize_folder
from ragpdf.core.projects import DocumentSettings, Project, clean_columns
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import format_pages
from ragpdf.core.workspace import Workspace
from ragpdf.indexing.spec import read_csv_rows
from ragpdf.library_analysis import MAX_INSTRUCTIONS_CHARS, InvalidAnswerError, _describe_errors, clean_instructions

if TYPE_CHECKING:  # solo per i tipi: app.py non importa questo modulo
    from ragpdf.app import App

MAX_CANDIDATES = 300              # cataloghi elaborati per richiesta (solo testo)
MAX_DESCRIPTION_CHARS = 1000      # descrizione di ogni candidato nel prompt
CSV_SAMPLE_ROWS = 5
VERDICTS = ("compatibile", "parziale")
SKIPPED_TAGS = {"copertina", "vuota"}   # sezioni inutili per cercare i dati


class GuidedError(LibraryError):
    """Richiesta o azione non valida sul progetto guidato, con messaggio per l'utente."""


def is_elaborated(card: LibraryCard) -> bool:
    """Un catalogo elaborato ha almeno una descrizione o dei dati presenti."""
    return bool(card.description.strip() or card.fields)


# ---------------------------------------------------------------------------------- richiesta
class GuidedRequest(BaseModel):
    """L'indice che l'utente vuole: uno o più modi di descriverlo."""

    row: str = ""
    columns: list[dict[str, str]] = Field(default_factory=list)
    text: str = ""
    reference: str | None = None          # percorso di un CSV d'esempio (cartella references/)
    folder: str = ""                       # dove cercare: "" = tutta la biblioteca

    def is_empty(self) -> bool:
        return not (self.row.strip() or self.columns or self.text.strip() or self.reference)


def describe_request(request: GuidedRequest) -> str:
    parts = []
    if request.row.strip():
        parts.append(f"- Ogni riga rappresenta: {request.row.strip()}")
    if request.columns:
        parts.append("- Colonne indicate dall'utente (tienile con questi nomi e in quest'ordine):\n"
                     + "\n".join(f"  {i}. {c['name']}" + (f": {c['description']}" if c.get("description") else "")
                                 for i, c in enumerate(request.columns, 1)))
    if request.reference:
        path = Path(request.reference)
        columns, rows = read_csv_rows(path)
        sample = "\n".join(" | ".join(str(r.get(c, "")) for c in columns) for r in rows[:CSV_SAMPLE_ROWS])
        parts.append(f"- CSV d'esempio ({path.name}), colonne: {', '.join(columns)}\n"
                     f"  Prime righe:\n```\n{' | '.join(columns)}\n{sample}\n```")
    if request.text.strip():
        parts.append(f"- A parole: {request.text.strip()}")
    return "\n".join(parts)


def _collections(card: LibraryCard) -> list[Any]:
    return [c for c in card.collections if c.tag not in SKIPPED_TAGS]


def describe_catalog(code: str, doc: Document, card: LibraryCard, max_description: int | None = None) -> str:
    lines = [f"### {code} — {doc.info.title} ({doc.info.page_count} pagine)"]
    description = card.description.strip()
    if description:
        if max_description and len(description) > max_description:
            description = description[:max_description].rstrip() + " [...]"
        lines.append(f"- Descrizione: {description}")
    if card.fields:
        lines.append("- Dati presenti: " + "; ".join(f.name + (f" ({f.description})" if f.description else "")
                                                     for f in card.fields))
    for n, c in enumerate(_collections(card), 1):
        pages = f"pagina {c.start}" if c.start == c.end else f"pagine {c.start}-{c.end}"
        lines.append(f"- Sezione {code}/{n}: {pages}, {PAGE_TAGS[c.tag][0]}" + (f" — {c.comment}" if c.comment else ""))
    return "\n".join(lines)


class Prepared(BaseModel):
    """Ciò che verrebbe inviato, senza chiamare Gemini."""

    prompt: str
    codes: dict[str, str]                       # "C1" -> id del documento ("P" = partenza)
    sections: dict[str, tuple[str, str]]        # "C1/2" -> (id del documento, id della collezione)
    candidates: list[str]                       # id dei cataloghi candidati
    not_elaborated: int                         # cataloghi esclusi perché non elaborati


def prepare(app: App, seed: Document, request: GuidedRequest, instructions: str) -> Prepared:
    if request.is_empty():
        raise GuidedError("Descrivi l'indice che vuoi: cosa è una riga, le colonne, un CSV d'esempio o a parole.")
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        raise GuidedError(f"Le indicazioni sono troppo lunghe ({len(instructions)} caratteri, massimo "
                          f"{MAX_INSTRUCTIONS_CHARS}).")
    folder = normalize_folder(request.folder)
    others, excluded = [], 0
    for doc in app.documents.list():
        if doc.id == seed.id:
            continue
        card = app.library.card(doc)
        if folder and not (card.folder == folder or card.folder.startswith(folder + "/")):
            continue
        if is_elaborated(card):
            others.append((doc, card))
        else:
            excluded += 1
    if len(others) > MAX_CANDIDATES:
        raise GuidedError(f"Ci sono {len(others)} cataloghi elaborati: il massimo per una richiesta è "
                          f"{MAX_CANDIDATES}. Restringi la ricerca a una cartella.")
    codes = {"P": seed.id}
    sections: dict[str, tuple[str, str]] = {}
    seed_card = app.library.card(seed)
    for n, c in enumerate(_collections(seed_card), 1):
        sections[f"P/{n}"] = (seed.id, c.id)
    blocks = []
    for i, (doc, card) in enumerate(others, 1):
        code = f"C{i}"
        codes[code] = doc.id
        for n, c in enumerate(_collections(card), 1):
            sections[f"{code}/{n}"] = (doc.id, c.id)
        blocks.append(describe_catalog(code, doc, card, MAX_DESCRIPTION_CHARS))
    prompt = render_prompt(
        load_prompt("library_guided_frame"),
        instructions=clean_instructions(instructions) or load_prompt("library_guided").strip(),
        request=describe_request(request),
        seed=describe_catalog("P", seed, seed_card),
        count=len(others),
        catalogs="\n\n".join(blocks) if blocks else "(nessun altro catalogo elaborato: valuta solo quello di partenza)",
    ).strip() + "\n"
    return Prepared(prompt=prompt, codes=codes, sections=sections, candidates=[d.id for d, _ in others],
                    not_elaborated=excluded)


def guided_schema(prepared: Prepared) -> dict[str, Any]:
    def enum(values: list[str]) -> dict[str, Any]:
        return {"type": "string", "enum": values} if values else {"type": "string"}

    candidate_codes = [c for c in prepared.codes if c != "P"]
    seed_sections = [s for s in prepared.sections if s.startswith("P/")]
    other_sections = [s for s in prepared.sections if not s.startswith("P/")]
    column = {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
              "required": ["name", "description"]}
    strings = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "table": {"type": "object", "properties": {
                "description": {"type": "string"}, "row": {"type": "string"},
                "columns": {"type": "array", "items": column}},
                "required": ["description", "row", "columns"]},
            "seed": {"type": "object", "properties": {
                "covered": strings, "missing": strings,
                "collections": {"type": "array", "items": enum(seed_sections)}, "notes": {"type": "string"}},
                "required": ["covered", "missing", "collections", "notes"]},
            "candidates": {"type": "array", "items": {"type": "object", "properties": {
                "catalog": enum(candidate_codes), "verdict": {"type": "string", "enum": list(VERDICTS)},
                "reason": {"type": "string"}, "covered": strings,
                "collections": {"type": "array", "items": enum(other_sections)}},
                "required": ["catalog", "verdict", "reason", "covered", "collections"]}},
            "project_name": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["table", "seed", "candidates", "project_name", "notes"],
    }


# ---------------------------------------------------------------------------------- risposta
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Column(_Strict):
    name: str
    description: str


class _Table(_Strict):
    description: str
    row: str
    columns: list[_Column]


class _Seed(_Strict):
    covered: list[str]
    missing: list[str]
    collections: list[str]
    notes: str


class _Candidate(_Strict):
    catalog: str
    verdict: str
    reason: str
    covered: list[str]
    collections: list[str]


class _Answer(_Strict):
    table: _Table
    seed: _Seed
    candidates: list[_Candidate]
    project_name: str
    notes: str


class CandidateResult(BaseModel):
    document: str
    verdict: str
    reason: str = ""
    covered: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)      # id delle collezioni della scheda


class GuidedResult(BaseModel):
    description: str = ""
    row: str = ""
    columns: list[dict[str, str]] = Field(default_factory=list)
    seed_covered: list[str] = Field(default_factory=list)
    seed_missing: list[str] = Field(default_factory=list)
    seed_collections: list[str] = Field(default_factory=list)
    seed_notes: str = ""
    candidates: list[CandidateResult] = Field(default_factory=list)
    project_name: str = ""
    notes: str = ""


def validate(raw: Any, prepared: Prepared) -> tuple[GuidedResult, list[str]]:
    """Struttura rigorosa (altrimenti :class:`InvalidAnswerError`), poi pulizia del contenuto.

    Non valida se: manca o avanza un campo, un tipo è sbagliato, un codice di catalogo o di sezione
    non esiste, un verdetto è diverso da compatibile/parziale, un candidato compare due volte o è il
    catalogo di partenza. Corrette e segnalate: colonne vuote o doppie, nomi di colonna sconosciuti
    nelle coperture, sezioni attribuite al catalogo sbagliato.
    """
    if not isinstance(raw, dict):
        raise InvalidAnswerError([f"atteso un oggetto JSON, ricevuto {type(raw).__name__}"])
    try:
        answer = _Answer.model_validate(raw)
    except ValidationError as e:
        raise InvalidAnswerError(_describe_errors(e)) from e

    errors, seen = [], set()
    for i, c in enumerate(answer.candidates):
        if c.catalog == "P":
            errors.append(f"candidates.{i}: il catalogo di partenza non va tra i candidati")
        elif c.catalog not in prepared.codes:
            errors.append(f"candidates.{i}: codice di catalogo sconosciuto '{c.catalog}'")
        elif c.catalog in seen:
            errors.append(f"candidates.{i}: il catalogo {c.catalog} compare due volte")
        seen.add(c.catalog)
        if c.verdict not in VERDICTS:
            errors.append(f"candidates.{i}.verdict: '{c.verdict}' non è compatibile o parziale")
        errors += [f"candidates.{i}.collections: sezione sconosciuta '{s}'" for s in c.collections
                   if s not in prepared.sections]
    errors += [f"seed.collections: sezione sconosciuta '{s}'" for s in answer.seed.collections
               if s not in prepared.sections]
    if errors:
        raise InvalidAnswerError(errors)

    problems: list[str] = []
    columns = []
    seen_names: set[str] = set()
    for c in answer.table.columns:
        name = " ".join(c.name.split())
        if not name or name.casefold() in seen_names:
            problems.append(f"colonna '{c.name}' vuota o ripetuta: tolta")
            continue
        seen_names.add(name.casefold())
        columns.append({"name": name, "description": " ".join(c.description.split())})
    by_norm = {c["name"].casefold(): c["name"] for c in columns}

    def known(names: list[str], where: str) -> list[str]:
        out = []
        for n in names:
            key = " ".join(n.split()).casefold()
            if key in by_norm:
                out.append(by_norm[key])
            else:
                problems.append(f"{where}: '{n}' non è una colonna della tabella, ignorata")
        return out

    def sections_of(codes: list[str], doc_id: str, where: str) -> list[str]:
        out = []
        for code in codes:
            owner, coll_id = prepared.sections[code]
            if owner != doc_id:
                problems.append(f"{where}: la sezione {code} è di un altro catalogo, ignorata")
            elif coll_id not in out:
                out.append(coll_id)
        return out

    seed_id = prepared.codes["P"]
    result = GuidedResult(
        description=answer.table.description.strip(), row=" ".join(answer.table.row.split()), columns=columns,
        seed_covered=known(answer.seed.covered, "catalogo di partenza"),
        seed_missing=known(answer.seed.missing, "catalogo di partenza"),
        seed_collections=sections_of(answer.seed.collections, seed_id, "catalogo di partenza"),
        seed_notes=answer.seed.notes.strip(),
        candidates=[CandidateResult(document=prepared.codes[c.catalog], verdict=c.verdict, reason=c.reason.strip(),
                                    covered=known(c.covered, c.catalog),
                                    collections=sections_of(c.collections, prepared.codes[c.catalog], c.catalog))
                    for c in answer.candidates],
        project_name=" ".join(answer.project_name.split()), notes=answer.notes.strip(),
    )
    if not result.columns:
        problems.append("Gemini non ha proposto colonne: scrivile tu nel report")
    return result, problems


# ---------------------------------------------------------------------------------- archivio
class GuidedRun(BaseModel):
    id: str
    created_at: str = Field(default_factory=utc_now)
    seed: str
    request: GuidedRequest
    instructions: str = ""
    candidates: list[str] = Field(default_factory=list)       # cataloghi inviati come candidati
    result: GuidedResult = Field(default_factory=GuidedResult)
    problems: list[str] = Field(default_factory=list)
    status: str = "valida"                                     # valida | non_valida
    raw: Any = None
    project_id: str | None = None


def runs_dir(workspace: Workspace) -> Path:
    return workspace.root / "library" / "guided"


def list_runs(workspace: Workspace, seed: str | None = None) -> list[GuidedRun]:
    folder = runs_dir(workspace)
    if not folder.exists():
        return []
    runs = [GuidedRun.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in folder.glob("*.json")]
    return sorted([r for r in runs if seed is None or r.seed == seed], key=lambda r: r.id, reverse=True)


def get_run(workspace: Workspace, run_id: str) -> GuidedRun:
    path = runs_dir(workspace) / f"{Path(run_id).name}.json"
    if not path.exists():
        raise GuidedError("Proposta non trovata.")
    return GuidedRun.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_run(workspace: Workspace, run: GuidedRun) -> None:
    write_json(runs_dir(workspace) / f"{run.id}.json", run.model_dump())


def run_guided(app: App, client: LLMClient, seed: Document, request: GuidedRequest, instructions: str,
               progress: Callable[[str], None] | None = None) -> GuidedRun:
    prepared = prepare(app, seed, request, instructions)
    stamp = utc_now().replace(":", "").replace("-", "").replace("+0000", "")
    existing = {r.id for r in list_runs(app.workspace)}
    run_id, n = stamp, 2
    while run_id in existing:
        run_id, n = f"{stamp}-{n}", n + 1
    base = {"id": run_id, "seed": seed.id, "request": request, "instructions": instructions,
            "candidates": prepared.candidates}
    if progress:
        progress(f"Gemini confronta la richiesta con {len(prepared.candidates)} cataloghi…")
    raw = client.generate_json(prepared.prompt, guided_schema(prepared))
    try:
        result, problems = validate(raw, prepared)
    except InvalidAnswerError as e:
        save_run(app.workspace, GuidedRun(**base, status="non_valida", problems=e.errors, raw=raw))
        raise
    run = GuidedRun(**base, result=result, problems=problems)
    save_run(app.workspace, run)
    return run


# ---------------------------------------------------------------------------------- creazione del progetto
def create_project(app: App, run: GuidedRun, name: str, description: str, row: str, columns: list[dict],
                   instructions: str, documents: list[dict]) -> Project:
    """Crea il progetto dal report: tabella come impostazioni globali, pagine dalle sezioni scelte.

    ``documents``: ``[{"id": ..., "collections": [...]}, ...]``, il catalogo di partenza compreso.
    """
    if run.status != "valida":
        raise GuidedError("Questa proposta non è valida (la risposta non rispettava il formato).")
    if run.project_id and app.projects.find(run.project_id):
        raise GuidedError(f"Il progetto di questa proposta esiste già ('{run.project_id}').")
    name = " ".join((name or "").split())
    if not name:
        raise GuidedError("Dai un nome al progetto.")
    chosen = [d for d in documents if d.get("id")]
    if not any(d["id"] == run.seed for d in chosen):
        raise GuidedError("Il catalogo di partenza deve far parte del progetto.")
    docs = [app.documents.get(str(d["id"])) for d in chosen]
    project = app.projects.create(name, [d.id for d in docs])
    info = project.info
    info.description, info.row = description.strip(), " ".join(row.split())
    info.fields, info.instructions = clean_columns(columns), instructions.strip()
    for doc, entry in zip(docs, chosen, strict=True):
        ids = [str(c) for c in entry.get("collections") or []]
        pages, missing = app.pages_from_collections(doc, ids)
        if pages:
            project.set_settings(doc.id, DocumentSettings(pages=format_pages(pages),
                                                          collections=[i for i in ids if i not in missing]))
        else:
            app.link_library_pages(project, doc)          # di default le collezioni "dati"
    if run.request.reference:
        info.reference = run.request.reference
    project.save()
    run.project_id = project.id
    save_run(app.workspace, run)
    return project
