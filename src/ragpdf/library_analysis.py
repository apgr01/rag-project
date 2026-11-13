"""Analisi di un catalogo della biblioteca con Gemini.

Gemini guarda le pagine scelte dall'utente (un *estratto* PDF, con la corrispondenza delle
pagine nel prompt) oppure il PDF intero, e propone descrizione, tag, campi e collezioni di
pagine. La risposta è un JSON a schema rigido, controllato prima dell'uso; diventa una
*proposta* salvata, che l'utente applica in tutto o in parte (niente viene scritto da solo).

Il prompt ha due parti:

- le **istruzioni**, modificabili dall'utente (predefinite in
  ``configs/prompts/library_analyze.md``, personali in ``data/library/analysis_prompt.md``);
- la **cornice tecnica** fissa (``library_analyze_frame.md``): pagine inviate, tag
  disponibili, scheda attuale, regole sui numeri di pagina. Così modificare le istruzioni
  non può rompere il formato della risposta.

Le analisi stanno in ``data/documents/<id>/analyses/<id>.json``: una richiesta pagata non
va mai persa.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ragpdf.core.documents import Document
from ragpdf.core.fileio import atomic_write_text, utc_now, write_json
from ragpdf.core.gemini import LLMClient
from ragpdf.core.library import PAGE_TAGS, FieldDef, Library, LibraryCard, LibraryError, PageCollection, normalize_tags
from ragpdf.core.pdf_tools import build_subset_pdf, page_mapping_text
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import format_pages
from ragpdf.core.workspace import Workspace
from ragpdf.primer import sample_pages

# Limiti di Gemini per i documenti PDF (con un piccolo margine di sicurezza).
MAX_PDF_PAGES = 1000
MAX_PDF_BYTES = 48 * 1024 * 1024
HEAVY_PDF_BYTES = 20 * 1024 * 1024        # oltre: avviso (upload più lento, più token)

QUICK_FIRST, QUICK_TOTAL = 6, 14          # selezione rapida: prime 6 + altre distribuite

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "Descrizione del catalogo, in italiano."},
        "tags": {"type": "array", "items": {"type": "string"}},
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name", "description"],
            },
        },
        "collections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "Prima pagina (numerazione originale)."},
                    "end": {"type": "integer", "description": "Ultima pagina (numerazione originale)."},
                    "tag": {"type": "string", "enum": list(PAGE_TAGS)},
                    "comment": {"type": "string"},
                    "deduced": {"type": "boolean", "description": "true se le pagine non erano nell'allegato."},
                },
                "required": ["start", "end", "tag", "comment", "deduced"],
            },
        },
        "notes": {"type": "string", "description": "Osservazioni, dubbi, cose da verificare."},
    },
    "required": ["description", "tags", "fields", "collections", "notes"],
}


class AnalysisError(LibraryError):
    """Richiesta di analisi non valida (pagine, dimensioni), con messaggio per l'utente."""


def quick_pages(page_count: int) -> list[int]:
    """Selezione rapida: le prime 6 pagine (copertina, indice, legende) più 8 distribuite."""
    return sample_pages(page_count, first=QUICK_FIRST, total=QUICK_TOTAL)


# ---------------------------------------------------------------------------------- prompt
class PromptStore:
    """Istruzioni dell'analisi: predefinite di fabbrica o personali dell'utente."""

    def __init__(self, workspace: Workspace):
        self.path = workspace.root / "library" / "analysis_prompt.md"

    @staticmethod
    def factory() -> str:
        return load_prompt("library_analyze").strip()

    def custom(self) -> str | None:
        return self.path.read_text(encoding="utf-8").strip() if self.path.exists() else None

    def effective(self) -> str:
        return self.custom() or self.factory()

    def save(self, text: str) -> None:
        text = text.strip()
        if not text or text == self.factory():
            self.path.unlink(missing_ok=True)      # vuoto o uguale: si torna a quello di fabbrica
        else:
            atomic_write_text(self.path, text + "\n")


def describe_card(card: LibraryCard) -> str:
    """La scheda attuale, da mostrare a Gemini perché la completi invece di ripartire da zero."""
    if not (card.description or card.tags or card.fields or card.collections):
        return "**Scheda attuale**: vuota (il catalogo non è ancora stato descritto)."
    lines = ["**Scheda attuale** (scritta dall'utente: completala e correggila se vedi errori, "
             "senza cancellare informazioni corrette):"]
    if card.description:
        lines.append(f"- Descrizione: {card.description}")
    if card.tags:
        lines.append(f"- Tag: {', '.join(card.tags)}")
    if card.fields:
        lines.append("- Campi: " + "; ".join(f.name + (f" ({f.description})" if f.description else "")
                                             for f in card.fields))
    for c in card.collections:
        pages = f"{c.start}" if c.start == c.end else f"{c.start}-{c.end}"
        lines.append(f"- Pagine {pages}: {c.tag}" + (f" — {c.comment}" if c.comment else ""))
    return "\n".join(lines)


def clean_instructions(text: str) -> str:
    """Le indicazioni dell'utente, senza i delimitatori del prompt (non si può "uscire" dal blocco)."""
    return text.replace("«««", "").replace("»»»", "").strip()


def build_prompt(doc: Document, card: LibraryCard, instructions: str, pages: list[int] | None,
                 include_card: bool = True) -> str:
    if pages is None:
        note = f"L'allegato è il documento COMPLETO ({doc.info.page_count} pagine)."
    else:
        note = page_mapping_text(pages, doc.info.page_count)
    tags = "\n".join(f"- `{k}`: {label} — {help_}" for k, (label, help_) in PAGE_TAGS.items())
    return render_prompt(
        load_prompt("library_analyze_frame"),
        instructions=clean_instructions(instructions) or PromptStore.factory(),
        title=doc.info.title, file_name=doc.info.source_filename, page_count=doc.info.page_count,
        pages_note=note, page_tags=tags,
        current_card=describe_card(card) if include_card else "",
    ).strip() + "\n"


def check_pages(doc: Document, pages: list[int] | None) -> list[int] | None:
    """Controlla la richiesta: ``None`` = PDF intero, altrimenti pagine ordinate senza doppioni."""
    total = doc.info.page_count
    if pages is None:
        if total > MAX_PDF_PAGES:
            raise AnalysisError(f"Il PDF ha {total} pagine: Gemini ne accetta al massimo {MAX_PDF_PAGES} "
                                "per richiesta. Scegli le pagine da inviare.")
        if doc.pdf_path.stat().st_size > MAX_PDF_BYTES:
            raise AnalysisError("Il PDF è troppo grande per una sola richiesta "
                                f"({doc.pdf_path.stat().st_size / 2**20:.0f} MB): scegli le pagine da inviare.")
        return None
    chosen = sorted(set(int(p) for p in pages))
    if not chosen:
        raise AnalysisError("Scegli le pagine da inviare (oppure tutto il PDF).")
    if chosen[0] < 1 or chosen[-1] > total:
        raise AnalysisError(f"Pagine fuori dal documento (1-{total}).")
    if len(chosen) > MAX_PDF_PAGES:
        raise AnalysisError(f"Al massimo {MAX_PDF_PAGES} pagine per richiesta.")
    return chosen


def attachment(doc: Document, pages: list[int] | None, folder: Path) -> Path:
    """Il PDF da inviare (intero o estratto), con il controllo della dimensione."""
    if pages is None:
        return doc.pdf_path
    path = build_subset_pdf(doc.pdf_path, pages, folder / "estratto.pdf")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise AnalysisError(f"L'estratto di {len(pages)} pagine pesa {path.stat().st_size / 2**20:.0f} MB, "
                            "troppo per una sola richiesta: scegli meno pagine.")
    return path


def attachment_size(doc: Document, pages: list[int] | None) -> int:
    if pages is None:
        return doc.pdf_path.stat().st_size
    with tempfile.TemporaryDirectory() as tmp:
        return build_subset_pdf(doc.pdf_path, pages, Path(tmp) / "estratto.pdf").stat().st_size


# ---------------------------------------------------------------------------------- risposta
MAX_INSTRUCTIONS_CHARS = 8000


class InvalidAnswerError(AnalysisError):
    """La risposta di Gemini non rispetta la struttura richiesta: non va usata."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("La risposta di Gemini non rispetta il formato richiesto ed è stata scartata "
                         f"({'; '.join(errors[:3])}{' ...' if len(errors) > 3 else ''}). "
                         "Resta in archivio come 'non valida': riprova, eventualmente con meno pagine "
                         "o con istruzioni più semplici.")


class _Strict(BaseModel):
    # rigoroso: niente campi in più, niente conversioni di tipo ("3" non è un numero)
    model_config = ConfigDict(extra="forbid", strict=True)


class _AnswerField(_Strict):
    name: str
    description: str


class _AnswerCollection(_Strict):
    start: int
    end: int
    tag: str
    comment: str
    deduced: bool

    @field_validator("tag")
    @classmethod
    def _known_tag(cls, v: str) -> str:
        if v not in PAGE_TAGS:
            raise ValueError(f"tag di pagina sconosciuto '{v}'")
        return v


class _Answer(_Strict):
    """La struttura esatta della risposta (la stessa di ANALYSIS_SCHEMA)."""

    description: str
    tags: list[str]
    fields: list[_AnswerField]
    collections: list[_AnswerCollection]
    notes: str


_ERROR_TEXT = {
    "missing": "campo mancante",
    "extra_forbidden": "campo non previsto",
    "string_type": "deve essere un testo",
    "int_type": "deve essere un numero intero",
    "bool_type": "deve essere vero o falso",
    "list_type": "deve essere un elenco",
    "dict_type": "deve essere un oggetto",
    "model_type": "deve essere un oggetto",
}


def _describe_errors(error: ValidationError) -> list[str]:
    """Errori di struttura in italiano, con la posizione (es. ``collections.0.start: ...``)."""
    out = []
    for e in error.errors():
        where = ".".join(str(x) for x in e.get("loc", ())) or "risposta"
        kind = e.get("type", "")
        if kind == "value_error":
            text = str(e.get("ctx", {}).get("error", e.get("msg", "non valido")))
        else:
            text = _ERROR_TEXT.get(kind, e.get("msg", "non valido"))
        out.append(f"{where}: {text}")
    return out


def validate(raw: Any, page_count: int) -> tuple[dict[str, Any], list[str]]:
    """Controlla la risposta di Gemini; restituisce ``(proposta, voci scartate)``.

    1. **Struttura** (rigorosa): deve essere esattamente l'oggetto richiesto, con tutti i campi,
       i tipi giusti, nessun campo in più e solo tag di pagina esistenti. Altrimenti
       :class:`InvalidAnswerError`: la risposta non viene usata.
    2. **Contenuto**: con la struttura a posto, le singole voci non utilizzabili (pagine fuori dal
       documento, campi senza nome o doppi) vengono scartate e segnalate.
    """
    if not isinstance(raw, dict):
        raise InvalidAnswerError([f"atteso un oggetto JSON, ricevuto {type(raw).__name__}"])
    try:
        answer = _Answer.model_validate(raw)
    except ValidationError as e:
        raise InvalidAnswerError(_describe_errors(e)) from e

    problems: list[str] = []
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for f in answer.fields:
        name = " ".join(f.name.split())
        if not name:
            problems.append("campo senza nome, scartato")
            continue
        if name.casefold() in seen:
            problems.append(f"campo '{name}' ripetuto, tenuto una volta")
            continue
        seen.add(name.casefold())
        fields.append({"name": name, "description": " ".join(f.description.split())})
    collections = []
    for c in answer.collections:
        first, last = min(c.start, c.end), max(c.start, c.end)
        if first < 1 or last > page_count:
            problems.append(f"pagine {first}-{last} fuori dal documento (1-{page_count}), scartata")
            continue
        collections.append({"start": first, "end": last, "tag": c.tag,
                            "comment": " ".join(c.comment.split()), "deduced": c.deduced})
    collections.sort(key=lambda c: (c["start"], c["end"]))
    result = {
        "description": answer.description.strip(),
        "tags": normalize_tags([t.lower() for t in answer.tags]),
        "fields": fields,
        "collections": collections,
        "notes": answer.notes.strip(),
    }
    return result, problems


# ---------------------------------------------------------------------------------- archivio
class Analysis(BaseModel):
    id: str
    created_at: str = Field(default_factory=utc_now)
    pages: list[int] | None = None          # None = PDF intero
    instructions: str = ""
    include_card: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)
    status: str = "nuova"                    # nuova | applicata | scartata | non_valida
    applied_at: str | None = None
    raw: Any = None                          # risposta originale, conservata solo se non valida

    @property
    def pages_text(self) -> str:
        return "PDF intero" if self.pages is None else f"pagine {format_pages(self.pages)}"


def analyses_dir(doc: Document) -> Path:
    return doc.folder / "analyses"


def list_analyses(doc: Document) -> list[Analysis]:
    folder = analyses_dir(doc)
    if not folder.exists():
        return []
    items = [Analysis.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in folder.glob("*.json")]
    return sorted(items, key=lambda a: a.id, reverse=True)


def get_analysis(doc: Document, analysis_id: str) -> Analysis:
    path = analyses_dir(doc) / f"{Path(analysis_id).name}.json"
    if not path.exists():
        raise AnalysisError("Analisi non trovata.")
    return Analysis.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_analysis(doc: Document, analysis: Analysis) -> None:
    write_json(analyses_dir(doc) / f"{analysis.id}.json", analysis.model_dump())


def run_analysis(doc: Document, library: Library, client: LLMClient, instructions: str,
                 pages: list[int] | None, include_card: bool = True,
                 progress: Callable[[str], None] | None = None) -> Analysis:
    """Invia a Gemini, controlla la risposta e la salva come proposta (``status = nuova``)."""
    pages = check_pages(doc, pages)
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        raise AnalysisError(f"Le istruzioni sono troppo lunghe ({len(instructions)} caratteri, massimo "
                            f"{MAX_INSTRUCTIONS_CHARS}).")
    card = library.card(doc)
    prompt = build_prompt(doc, card, instructions, pages, include_card)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = attachment(doc, pages, Path(tmp))
        if progress:
            progress("Gemini sta leggendo il catalogo…")
        raw = client.generate_json(prompt, ANALYSIS_SCHEMA, pdf=pdf)
    stamp = utc_now().replace(":", "").replace("-", "").replace("+0000", "")
    existing = {a.id for a in list_analyses(doc)}
    analysis_id, n = stamp, 2
    while analysis_id in existing:
        analysis_id, n = f"{stamp}-{n}", n + 1
    base = {"id": analysis_id, "pages": pages, "instructions": instructions, "include_card": include_card}
    try:
        result, problems = validate(raw, doc.info.page_count)
    except InvalidAnswerError as e:
        # una richiesta pagata non si butta: la risposta resta consultabile, ma non si usa
        save_analysis(doc, Analysis(**base, status="non_valida", problems=e.errors, raw=raw))
        raise
    analysis = Analysis(**base, result=result, problems=problems)
    save_analysis(doc, analysis)
    return analysis


# ---------------------------------------------------------------------------------- applicazione
class Selection(BaseModel):
    """Cosa applicare della proposta: indici delle voci scelte dall'utente."""

    description: str = "none"               # none | replace | append
    notes: bool = False                     # aggiunge le osservazioni alla descrizione
    tags: list[str] = Field(default_factory=list)
    fields: list[int] = Field(default_factory=list)
    collections: list[int] = Field(default_factory=list)


def apply_analysis(doc: Document, library: Library, analysis: Analysis, selection: Selection) -> LibraryCard:
    """Applica alla scheda le voci scelte; i doppioni vengono saltati."""
    if analysis.status == "non_valida":
        raise AnalysisError("Questa analisi non è valida (la risposta non rispettava il formato): "
                            "non si può applicare.")
    result = analysis.result
    card = library.card(doc)

    texts = []
    if selection.description in ("replace", "append") and result.get("description"):
        texts.append(result["description"])
    if selection.notes and result.get("notes"):
        texts.append("Osservazioni di Gemini: " + result["notes"])
    if texts:
        new = "\n\n".join(texts)
        card.description = new if selection.description == "replace" or not card.description.strip() \
            else card.description.rstrip() + "\n\n" + new

    proposed_tags = {t.casefold(): t for t in result.get("tags", [])}
    card.tags = card.tags + [proposed_tags[t.casefold()] for t in selection.tags if t.casefold() in proposed_tags]

    by_name = {f.name.casefold(): f for f in card.fields}
    for i in selection.fields:
        if not 0 <= i < len(result.get("fields", [])):
            continue
        f = result["fields"][i]
        existing = by_name.get(f["name"].casefold())
        if existing is None:
            card.fields.append(FieldDef(name=f["name"], description=f["description"]))
            by_name[f["name"].casefold()] = card.fields[-1]
        elif not existing.description:
            existing.description = f["description"]

    present = {(c.start, c.end, c.tag) for c in card.collections}
    for i in selection.collections:
        if not 0 <= i < len(result.get("collections", [])):
            continue
        c = result["collections"][i]
        if (c["start"], c["end"], c["tag"]) in present:
            continue
        card.collections.append(PageCollection(start=c["start"], end=c["end"], tag=c["tag"], comment=c["comment"],
                                               origin="dedotta" if c["deduced"] else "gemini"))
        present.add((c["start"], c["end"], c["tag"]))

    library.save_card(doc, card)
    analysis.status, analysis.applied_at = "applicata", utc_now()
    save_analysis(doc, analysis)
    return card
