"""Biblioteca dei cataloghi: cartelle, tag, descrizione, campi e collezioni di pagine.

È il posto dove i PDF "vivono" e si studiano prima di decidere come indicizzarli; i
progetti li usano, ma non li possiedono. Accanto a ``document.yaml`` ogni documento ha
una *scheda* della biblioteca::

    data/documents/<id>/library.yaml     scheda (vedi LibraryCard)
    data/library/folders.yaml            elenco delle cartelle (anche quelle vuote)

Le cartelle sono percorsi testuali (``Motori/Lombardini``): ogni catalogo sta in una sola
cartella (``""`` = cartella principale). I tag del catalogo sono liberi; i tag delle
*collezioni di pagine* invece sono fissi (PAGE_TAGS), perché servono al programma:
ad esempio le pagine ``da-indicizzare`` e ``esempio`` precompilano i progetti.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from ragpdf.core.documents import Document, DocumentStore
from ragpdf.core.fileio import atomic_write_text, utc_now
from ragpdf.core.workspace import Workspace

# Tag fissi delle collezioni di pagine: id -> (etichetta, spiegazione). L'ordine è quello
# mostrato nell'interfaccia.
PAGE_TAGS: dict[str, tuple[str, str]] = {
    "da-indicizzare": ("Da indicizzare", "Pagine con i dati da estrarre nel CSV"),
    "esempio": ("Pagina d'esempio", "Pagine rappresentative, da mostrare a Gemini in configurazione"),
    "copertina": ("Copertina", "Copertina, frontespizio, retro"),
    "indice": ("Indice", "Sommario o indice analitico"),
    "introduzione": ("Introduzione", "Presentazione, istruzioni d'uso del catalogo"),
    "legenda": ("Legenda / simboli", "Spiegazione di simboli, abbreviazioni, codici"),
    "tabelle-tecniche": ("Tabelle tecniche", "Dati tecnici non da indicizzare (o non ancora)"),
    "disegni": ("Disegni / immagini", "Esplosi, foto, schemi"),
    "avvertenze": ("Avvertenze / note", "Note, condizioni, avvertenze"),
    "vuota": ("Pubblicità / vuota", "Pagine senza contenuto utile"),
    "altro": ("Altro", "Tutto il resto (spiegalo nel commento)"),
}


class LibraryError(ValueError):
    """Errore della biblioteca, con messaggio pensato per l'utente."""


def normalize_folder(path: str | None) -> str:
    """``" Motori / Lombardini/ "`` -> ``"Motori/Lombardini"``; ``""`` è la cartella principale."""
    parts = [p.strip() for p in str(path or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    for p in parts:
        if p in (".", ".."):
            raise LibraryError(f"Nome di cartella non valido: '{p}'.")
    return "/".join(parts)


def normalize_tags(tags: list[str] | None) -> list[str]:
    """Tag senza spazi superflui e senza doppioni (maiuscole/minuscole non contano)."""
    result: list[str] = []
    seen: set[str] = set()
    for t in tags or []:
        tag = " ".join(str(t).split())
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            result.append(tag)
    return result


class FieldDef(BaseModel):
    """Un campo dello schema di indicizzazione: una colonna del futuro CSV."""

    name: str
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Ogni campo deve avere un nome.")
        return v


class PageCollection(BaseModel):
    """Un intervallo continuo di pagine con un tag fisso e un commento facoltativo."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    start: int
    end: int
    tag: str
    comment: str = ""
    created_at: str = Field(default_factory=utc_now)

    @field_validator("tag")
    @classmethod
    def _tag(cls, v: str) -> str:
        if v not in PAGE_TAGS:
            raise ValueError(f"Tag di pagina sconosciuto: '{v}'.")
        return v

    @property
    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))


class LibraryCard(BaseModel):
    folder: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    fields: list[FieldDef] = Field(default_factory=list)
    collections: list[PageCollection] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)

    def pages_with_tag(self, tag: str) -> list[int]:
        pages = {p for c in self.collections if c.tag == tag for p in c.pages}
        return sorted(pages)


class Library:
    def __init__(self, workspace: Workspace, documents: DocumentStore):
        self.workspace = workspace
        self.documents = documents

    @property
    def folders_path(self) -> Path:
        return self.workspace.root / "library" / "folders.yaml"

    # --- schede ------------------------------------------------------------------------
    @staticmethod
    def card_path(doc: Document) -> Path:
        return doc.folder / "library.yaml"

    def has_card(self, doc: Document) -> bool:
        return self.card_path(doc).exists()

    def card(self, doc: Document) -> LibraryCard:
        path = self.card_path(doc)
        if not path.exists():
            return LibraryCard()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return LibraryCard.model_validate(data)

    def save_card(self, doc: Document, card: LibraryCard) -> LibraryCard:
        card.folder = normalize_folder(card.folder)
        card.tags = normalize_tags(card.tags)
        card.collections.sort(key=lambda c: (c.start, c.end))
        card.updated_at = utc_now()
        if card.folder:
            self._remember_folder(card.folder)
        text = yaml.safe_dump(card.model_dump(), allow_unicode=True, sort_keys=False)
        atomic_write_text(self.card_path(doc), text)
        return card

    # --- collezioni di pagine ----------------------------------------------------------------
    def add_collection(self, doc: Document, start: int, end: int, tag: str, comment: str = "") -> PageCollection:
        start, end = self._check_range(doc, start, end)
        card = self.card(doc)
        coll = PageCollection(start=start, end=end, tag=tag, comment=comment.strip())
        card.collections.append(coll)
        self.save_card(doc, card)
        return coll

    def update_collection(self, doc: Document, coll_id: str, start: int, end: int, tag: str,
                          comment: str = "") -> PageCollection:
        start, end = self._check_range(doc, start, end)
        card = self.card(doc)
        coll = self._find_collection(card, coll_id)
        updated = PageCollection(id=coll.id, start=start, end=end, tag=tag, comment=comment.strip(),
                                 created_at=coll.created_at)
        card.collections = [updated if c.id == coll_id else c for c in card.collections]
        self.save_card(doc, card)
        return updated

    def remove_collection(self, doc: Document, coll_id: str) -> None:
        card = self.card(doc)
        self._find_collection(card, coll_id)
        card.collections = [c for c in card.collections if c.id != coll_id]
        self.save_card(doc, card)

    @staticmethod
    def _find_collection(card: LibraryCard, coll_id: str) -> PageCollection:
        for c in card.collections:
            if c.id == coll_id:
                return c
        raise LibraryError("Collezione di pagine non trovata (forse è già stata eliminata).")

    @staticmethod
    def _check_range(doc: Document, start: int, end: int) -> tuple[int, int]:
        start, end = int(start), int(end)
        if start > end:
            start, end = end, start
        if start < 1 or end > doc.info.page_count:
            raise LibraryError(f"Pagine fuori dal documento: {start}-{end} (il PDF ha "
                               f"{doc.info.page_count} pagine).")
        return start, end

    # --- cartelle ----------------------------------------------------------------------------
    def _stored_folders(self) -> list[str]:
        if not self.folders_path.exists():
            return []
        data = yaml.safe_load(self.folders_path.read_text(encoding="utf-8")) or []
        return [f for f in (normalize_folder(x) for x in data) if f]

    def _write_folders(self, folders: set[str]) -> None:
        text = yaml.safe_dump(sorted(folders, key=str.casefold), allow_unicode=True)
        atomic_write_text(self.folders_path, text)

    def _remember_folder(self, folder: str) -> None:
        known = set(self._stored_folders())
        wanted = {"/".join(folder.split("/")[:i + 1]) for i in range(folder.count("/") + 1)}
        if not wanted <= known:
            self._write_folders(known | wanted)

    def folders(self, cards: list[LibraryCard] | None = None) -> list[str]:
        """Tutte le cartelle: quelle salvate più quelle usate dalle schede (con i genitori)."""
        result = set(self._stored_folders())
        for card in cards if cards is not None else (self.card(d) for d in self.documents.list()):
            parts = card.folder.split("/") if card.folder else []
            result.update("/".join(parts[:i + 1]) for i in range(len(parts)))
        return sorted(result, key=str.casefold)

    def create_folder(self, path: str) -> str:
        folder = normalize_folder(path)
        if not folder:
            raise LibraryError("Dai un nome alla cartella.")
        self._remember_folder(folder)
        return folder

    def rename_folder(self, path: str, new_path: str) -> str:
        """Rinomina o sposta una cartella, con le sottocartelle e i cataloghi che contiene."""
        old, new = normalize_folder(path), normalize_folder(new_path)
        if not old or not new:
            raise LibraryError("Indica la cartella e il nuovo nome.")
        if new == old:
            return new
        if new.startswith(old + "/"):
            raise LibraryError("Non puoi spostare una cartella dentro sé stessa.")
        if new in self.folders():
            raise LibraryError(f"Esiste già una cartella '{new}'.")

        def moved(f: str) -> str:
            return new + f[len(old):] if f == old or f.startswith(old + "/") else f

        for doc in self.documents.list():
            card = self.card(doc)
            if card.folder and moved(card.folder) != card.folder:
                card.folder = moved(card.folder)
                self.save_card(doc, card)
        self._write_folders({moved(f) for f in self._stored_folders()} | {new})
        return new

    def delete_folder(self, path: str) -> None:
        """Elimina una cartella vuota (senza cataloghi, anche nelle sottocartelle)."""
        folder = normalize_folder(path)
        inside = [d for d in self.documents.list()
                  if (c := self.card(d).folder) == folder or c.startswith(folder + "/")]
        if inside:
            raise LibraryError(f"La cartella contiene {len(inside)} catalog{'o' if len(inside) == 1 else 'hi'}: "
                               "spostali o eliminali prima.")
        self._write_folders({f for f in self._stored_folders()
                             if f != folder and not f.startswith(folder + "/")})

    # --- operazioni su più cataloghi ---------------------------------------------------------
    def move(self, docs: list[Document], folder: str) -> str:
        folder = normalize_folder(folder)
        for doc in docs:
            card = self.card(doc)
            card.folder = folder
            self.save_card(doc, card)
        return folder

    def retag(self, docs: list[Document], add: list[str] | None = None, remove: list[str] | None = None) -> None:
        add, drop = normalize_tags(add), {t.casefold() for t in normalize_tags(remove)}
        for doc in docs:
            card = self.card(doc)
            card.tags = [t for t in card.tags if t.casefold() not in drop] + add
            self.save_card(doc, card)
