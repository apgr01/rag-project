"""Raggruppamento dei cataloghi in progetti, proposto da Gemini.

L'utente seleziona dei cataloghi nella biblioteca; Gemini ne riceve le schede (descrizione,
tag, campi, collezioni) e, se richiesto, una pagina d'esempio per catalogo come immagine. Una
sola richiesta: i gruppi si decidono confrontando tutti i cataloghi insieme.

Come nell'analisi (``library_analysis.py``), il prompt ha le **indicazioni** modificabili
dall'utente e una **cornice** fissa con il formato della risposta, che prevale. I cataloghi
sono indicati con codici brevi (``C1``, ``C2``...) che nello schema JSON sono un ``enum``: Gemini
non può citare cataloghi che non esistono. La risposta viene poi controllata in modo
rigoroso; se la struttura non torna è *non valida* e non si usa.

Il risultato è un **raggruppamento** salvato in ``data/library/groupings/<id>.json``: un report
su cui l'utente lavora (sposta cataloghi tra i gruppi, crea i progetti, assegna i tag).
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ragpdf.core.documents import Document
from ragpdf.core.fileio import utc_now, write_json
from ragpdf.core.gemini import LLMClient
from ragpdf.core.library import LibraryCard, LibraryError, normalize_tags
from ragpdf.core.pdf_tools import render_thumbnail
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import format_pages
from ragpdf.core.workspace import Workspace
from ragpdf.library_analysis import MAX_INSTRUCTIONS_CHARS, InvalidAnswerError, _describe_errors, clean_instructions

if TYPE_CHECKING:  # solo per i tipi: app.py non importa questo modulo
    from ragpdf.app import App

MIN_CATALOGS = 2
MAX_CATALOGS = 80                 # solo testo
MAX_CATALOGS_WITH_IMAGES = 40     # con una pagina d'esempio per catalogo
IMAGE_DPI = 100                   # basta per riconoscere il layout, pesa poco
MAX_DESCRIPTION_CHARS = 1500      # per catalogo, per non far esplodere il prompt

UNGROUPED = "ungrouped"
NEW_GROUP = "new"


class GroupingError(LibraryError):
    """Richiesta o azione non valida sul raggruppamento, con messaggio per l'utente."""


def grouping_schema(codes: list[str]) -> dict[str, Any]:
    code = {"type": "string", "enum": codes}
    return {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "catalogs": {"type": "array", "items": code},
                        "comment": {"type": "string"},
                        "common_tags": {"type": "array", "items": {"type": "string"}},
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                                "required": ["name", "description"],
                            },
                        },
                    },
                    "required": ["name", "catalogs", "comment", "common_tags", "fields"],
                },
            },
            "ungrouped": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"catalog": code, "reason": {"type": "string"}},
                    "required": ["catalog", "reason"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["groups", "ungrouped", "notes"],
    }


# ---------------------------------------------------------------------------------- prompt
def example_page(card: LibraryCard) -> int | None:
    """La pagina d'esempio di un catalogo: la prima "pagina d'esempio", altrimenti la prima da indicizzare."""
    for tag in ("esempio", "da-indicizzare"):
        pages = card.pages_with_tag(tag)
        if pages:
            return pages[0]
    return None


def is_studied(card: LibraryCard) -> bool:
    return bool(card.description.strip() or card.fields)


def describe_catalog(code: str, doc: Document, card: LibraryCard, projects: list[str]) -> str:
    lines = [f"### {code} — {doc.info.title}",
             f"- File: {doc.info.source_filename}, {doc.info.page_count} pagine"
             + (f", cartella: {card.folder}" if card.folder else "")]
    if projects:
        lines.append(f"- Già nei progetti: {', '.join(projects)}")
    if card.tags:
        lines.append(f"- Tag: {', '.join(card.tags)}")
    description = card.description.strip()
    if description:
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS].rstrip() + " [...]"
        lines.append(f"- Descrizione: {description}")
    if card.fields:
        lines.append("- Campi: " + "; ".join(f.name + (f" ({f.description})" if f.description else "")
                                             for f in card.fields))
    by_tag: dict[str, list[int]] = {}
    for c in card.collections:
        by_tag.setdefault(c.tag, []).extend(c.pages)
    if by_tag:
        lines.append("- Pagine: " + "; ".join(f"{tag} {format_pages(pages)}" for tag, pages in by_tag.items()))
    if not is_studied(card):
        lines.append("- (catalogo non ancora studiato: né descrizione né campi)")
    return "\n".join(lines)


class Prepared(BaseModel):
    """Ciò che verrebbe inviato: prompt, codici, immagini (senza chiamare Gemini)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    prompt: str
    codes: dict[str, str]                      # codice -> id del documento
    images: list[tuple[str, int]]              # (id del documento, pagina) nell'ordine delle immagini
    not_studied: list[str]


def prepare(app: App, docs: list[Document], instructions: str, with_images: bool) -> Prepared:
    if len(docs) < MIN_CATALOGS:
        raise GroupingError("Seleziona almeno 2 cataloghi da confrontare.")
    limit = MAX_CATALOGS_WITH_IMAGES if with_images else MAX_CATALOGS
    if len(docs) > limit:
        raise GroupingError(
            f"Hai selezionato {len(docs)} cataloghi: il massimo per una richiesta è {limit}"
            + (" con le pagine d'esempio" if with_images else "")
            + ". Restringi la selezione (per esempio filtrando per tag o per cartella).")
    if len(instructions) > MAX_INSTRUCTIONS_CHARS:
        raise GroupingError(f"Le indicazioni sono troppo lunghe ({len(instructions)} caratteri, massimo "
                            f"{MAX_INSTRUCTIONS_CHARS}).")
    projects = app.projects.list()
    codes: dict[str, str] = {}
    blocks, images, not_studied = [], [], []
    for i, doc in enumerate(docs, 1):
        code = f"C{i}"
        codes[code] = doc.id
        card = app.library.card(doc)
        names = [p.info.name for p in projects if doc.id in p.info.documents]
        blocks.append(describe_catalog(code, doc, card, names))
        if not is_studied(card):
            not_studied.append(doc.id)
        if with_images:
            page = example_page(card)
            if page is not None:
                images.append((doc.id, page))
    if with_images:
        code_of = {v: k for k, v in codes.items()}
        listed = "\n".join(f"- immagine {n}: {code_of[d]}, pagina {p}" for n, (d, p) in enumerate(images, 1))
        images_note = ("Sono allegate le immagini di una pagina d'esempio per i cataloghi che ne hanno una, "
                       "in quest'ordine:\n" + listed if images else
                       "Nessun catalogo ha pagine d'esempio segnate: non ci sono immagini allegate.")
    else:
        images_note = "Non ci sono immagini allegate: decidi in base alle schede dei cataloghi."
    prompt = render_prompt(
        load_prompt("library_group_frame"),
        instructions=clean_instructions(instructions) or load_prompt("library_group").strip(),
        count=len(docs), images_note=images_note, catalogs="\n\n".join(blocks),
    ).strip() + "\n"
    return Prepared(prompt=prompt, codes=codes, images=images, not_studied=not_studied)


# ---------------------------------------------------------------------------------- risposta
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Field(_Strict):
    name: str
    description: str


class _Group(_Strict):
    name: str
    catalogs: list[str]
    comment: str
    common_tags: list[str]
    fields: list[_Field]


class _Ungrouped(_Strict):
    catalog: str
    reason: str


class _Answer(_Strict):
    groups: list[_Group]
    ungrouped: list[_Ungrouped]
    notes: str


class GroupEntry(BaseModel):
    name: str
    documents: list[str]
    comment: str = ""
    common_tags: list[str] = Field(default_factory=list)
    fields: list[dict[str, str]] = Field(default_factory=list)
    project_id: str | None = None             # progetto creato da questo gruppo
    tags_applied: list[str] = Field(default_factory=list)


class UngroupedEntry(BaseModel):
    document: str
    reason: str = ""


def validate(raw: Any, codes: dict[str, str]) -> tuple[list[GroupEntry], list[UngroupedEntry], str, list[str]]:
    """Controlla la risposta: ``(gruppi, non accorpabili, osservazioni, voci corrette)``.

    **Struttura** (rigorosa, altrimenti :class:`InvalidAnswerError`): l'oggetto esatto richiesto,
    solo codici esistenti, nessun catalogo in due posti. **Contenuto** (corretto e segnalato):
    cataloghi dimenticati da Gemini e gruppi di un solo catalogo finiscono tra i non accorpabili.
    """
    if not isinstance(raw, dict):
        raise InvalidAnswerError([f"atteso un oggetto JSON, ricevuto {type(raw).__name__}"])
    try:
        answer = _Answer.model_validate(raw)
    except ValidationError as e:
        raise InvalidAnswerError(_describe_errors(e)) from e

    errors, seen = [], {}
    places = [(f"groups.{i}", code) for i, g in enumerate(answer.groups) for code in g.catalogs]
    places += [(f"ungrouped.{i}", u.catalog) for i, u in enumerate(answer.ungrouped)]
    for where, code in places:
        if code not in codes:
            errors.append(f"{where}: codice di catalogo sconosciuto '{code}'")
        elif code in seen:
            errors.append(f"{where}: il catalogo {code} compare anche in {seen[code]}")
        else:
            seen[code] = where
    if errors:
        raise InvalidAnswerError(errors)

    problems: list[str] = []
    groups: list[GroupEntry] = []
    ungrouped = [UngroupedEntry(document=codes[u.catalog], reason=u.reason.strip()) for u in answer.ungrouped]
    for n, g in enumerate(answer.groups, 1):
        name = " ".join(g.name.split()) or f"Gruppo {n}"
        docs = [codes[c] for c in g.catalogs]
        if len(docs) < 2:
            if docs:
                problems.append(f"gruppo '{name}' con un solo catalogo: spostato tra i non accorpabili")
                ungrouped.append(UngroupedEntry(document=docs[0],
                                                reason=g.comment.strip() or "gruppo di un solo catalogo"))
            else:
                problems.append(f"gruppo '{name}' vuoto: ignorato")
            continue
        fields, names = [], set()
        for f in g.fields:
            fname = " ".join(f.name.split())
            if fname and fname.casefold() not in names:
                names.add(fname.casefold())
                fields.append({"name": fname, "description": " ".join(f.description.split())})
        groups.append(GroupEntry(name=name, documents=docs, comment=g.comment.strip(),
                                 common_tags=normalize_tags([t.lower() for t in g.common_tags]), fields=fields))
    for code, doc_id in codes.items():
        if code not in seen:
            problems.append(f"{code} non classificato da Gemini: messo tra i non accorpabili")
            ungrouped.append(UngroupedEntry(document=doc_id, reason="non classificato da Gemini"))
    return groups, ungrouped, answer.notes.strip(), problems


# ---------------------------------------------------------------------------------- archivio
class Grouping(BaseModel):
    id: str
    created_at: str = Field(default_factory=utc_now)
    documents: list[str]                      # cataloghi inviati, nell'ordine dei codici
    instructions: str = ""
    with_images: bool = False
    groups: list[GroupEntry] = Field(default_factory=list)
    ungrouped: list[UngroupedEntry] = Field(default_factory=list)
    notes: str = ""
    problems: list[str] = Field(default_factory=list)
    status: str = "valida"                    # valida | non_valida
    raw: Any = None                           # risposta originale, conservata solo se non valida


def groupings_dir(workspace: Workspace) -> Path:
    return workspace.root / "library" / "groupings"


def list_groupings(workspace: Workspace) -> list[Grouping]:
    folder = groupings_dir(workspace)
    if not folder.exists():
        return []
    items = [Grouping.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in folder.glob("*.json")]
    return sorted(items, key=lambda g: g.id, reverse=True)


def get_grouping(workspace: Workspace, grouping_id: str) -> Grouping:
    path = groupings_dir(workspace) / f"{Path(grouping_id).name}.json"
    if not path.exists():
        raise GroupingError("Raggruppamento non trovato.")
    return Grouping.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_grouping(workspace: Workspace, grouping: Grouping) -> None:
    write_json(groupings_dir(workspace) / f"{grouping.id}.json", grouping.model_dump())


def run_grouping(app: App, client: LLMClient, docs: list[Document], instructions: str, with_images: bool,
                 progress: Callable[[str], None] | None = None) -> Grouping:
    prepared = prepare(app, docs, instructions, with_images)
    stamp = utc_now().replace(":", "").replace("-", "").replace("+0000", "")
    existing = {g.id for g in list_groupings(app.workspace)}
    grouping_id, n = stamp, 2
    while grouping_id in existing:
        grouping_id, n = f"{stamp}-{n}", n + 1
    base = {"id": grouping_id, "documents": [d.id for d in docs], "instructions": instructions,
            "with_images": with_images}
    by_id = {d.id: d for d in docs}
    with tempfile.TemporaryDirectory() as tmp:
        images = [render_thumbnail(by_id[d].pdf_path, p, Path(tmp) / f"{i:03d}.png", dpi=IMAGE_DPI)
                  for i, (d, p) in enumerate(prepared.images, 1)]
        if progress:
            progress(f"Gemini confronta {len(docs)} cataloghi…")
        raw = client.generate_json(prepared.prompt, grouping_schema(list(prepared.codes)), images=images)
    try:
        groups, ungrouped, notes, problems = validate(raw, prepared.codes)
    except InvalidAnswerError as e:
        save_grouping(app.workspace, Grouping(**base, status="non_valida", problems=e.errors, raw=raw))
        raise
    grouping = Grouping(**base, groups=groups, ungrouped=ungrouped, notes=notes, problems=problems)
    save_grouping(app.workspace, grouping)
    return grouping


# ---------------------------------------------------------------------------------- azioni sul report
def _usable(grouping: Grouping) -> None:
    if grouping.status != "valida":
        raise GroupingError("Questo raggruppamento non è valido (la risposta non rispettava il formato).")


def _group(grouping: Grouping, index: int) -> GroupEntry:
    if not 0 <= index < len(grouping.groups):
        raise GroupingError("Gruppo non trovato (forse il report è cambiato: ricarica la pagina).")
    return grouping.groups[index]


def move_document(grouping: Grouping, doc_id: str, target: str) -> None:
    """Sposta un catalogo in un altro gruppo (indice), tra i non accorpabili o in un nuovo gruppo."""
    _usable(grouping)
    reason = ""
    for g in grouping.groups:
        if doc_id in g.documents:
            if g.project_id:
                raise GroupingError(f"Il progetto del gruppo '{g.name}' è già stato creato: aggiungi o togli i "
                                    "PDF dal progetto.")
            g.documents.remove(doc_id)
            break
    else:
        entry = next((u for u in grouping.ungrouped if u.document == doc_id), None)
        if entry is None:
            raise GroupingError("Catalogo non presente in questo raggruppamento.")
        grouping.ungrouped.remove(entry)
        reason = entry.reason
    if target == UNGROUPED:
        grouping.ungrouped.append(UngroupedEntry(document=doc_id, reason=reason or "spostato a mano"))
    elif target == NEW_GROUP:
        grouping.groups.append(GroupEntry(name=f"Nuovo gruppo {len(grouping.groups) + 1}", documents=[doc_id],
                                          comment="creato a mano"))
    else:
        group = _group(grouping, int(target))
        if group.project_id:
            raise GroupingError(f"Il progetto del gruppo '{group.name}' è già stato creato.")
        group.documents.append(doc_id)
    grouping.groups = [g for g in grouping.groups if g.documents or g.project_id]


def update_group(grouping: Grouping, index: int, name: str | None = None, tags: list[str] | None = None) -> None:
    _usable(grouping)
    group = _group(grouping, index)
    if name is not None and " ".join(name.split()):
        group.name = " ".join(name.split())
    if tags is not None:
        group.common_tags = normalize_tags(tags)


def apply_group_tags(app: App, grouping: Grouping, index: int, tags: list[str]) -> list[str]:
    _usable(grouping)
    group = _group(grouping, index)
    tags = normalize_tags(tags)
    if not tags:
        raise GroupingError("Scrivi almeno un tag.")
    docs = [app.documents.get(d) for d in group.documents]
    app.library.retag(docs, add=tags)
    group.common_tags = tags
    group.tags_applied = normalize_tags(group.tags_applied + tags)
    return tags


def create_group_project(app: App, grouping: Grouping, index: int, name: str):
    """Crea il progetto con i cataloghi del gruppo; le colonne unificate diventano l'obiettivo proposto."""
    _usable(grouping)
    group = _group(grouping, index)
    if group.project_id and app.projects.find(group.project_id):
        raise GroupingError(f"Il progetto di questo gruppo esiste già ('{group.project_id}').")
    if not group.documents:
        raise GroupingError("Il gruppo non contiene cataloghi.")
    name = " ".join((name or group.name).split())
    docs = [app.documents.get(d) for d in group.documents]
    project = app.create_project_from_library(name, docs)
    project.info.fields = [dict(f) for f in group.fields]
    project.save()
    group.name, group.project_id = name, project.id
    return project
