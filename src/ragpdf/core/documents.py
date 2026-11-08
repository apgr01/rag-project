"""Registro dei documenti.

Ogni documento aggiunto al workspace ha una propria cartella::

    data/documents/<id>/
    ├── document.yaml        scheda del documento (vedi DocumentInfo)
    ├── source.pdf           copia del PDF originale
    ├── pages/               immagini delle pagine (cache del rendering)
    ├── extractions/<spec>/  risultati dell'indicizzazione, uno per pagina
    └── descriptions/        descrizioni delle pagine per la modalità RAG

L'``id`` è leggibile e ricavato dal nome del file (``catalogo-5-kubota-rel-1010``).
Oltre all'id si salva l'impronta SHA-256 del contenuto: permette di accorgersi
se si aggiunge di nuovo lo stesso PDF, magari con un altro nome.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pymupdf
import yaml
from pydantic import BaseModel

from ragpdf.core.fileio import atomic_write_text, utc_now
from ragpdf.core.workspace import Workspace, slugify


class DocumentInfo(BaseModel):
    id: str
    title: str
    source_filename: str
    sha256: str
    page_count: int
    primer: str | None = None
    added_at: str


class DocumentError(Exception):
    """Errore sui documenti, con messaggio pensato per l'utente."""


class DuplicateDocumentError(DocumentError):
    def __init__(self, existing: DocumentInfo):
        self.existing = existing
        super().__init__(
            f"Questo PDF è già presente nel workspace come '{existing.id}' "
            f"({existing.source_filename})."
        )


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Document:
    """Un documento del workspace: la sua scheda più i percorsi dei suoi file."""

    def __init__(self, info: DocumentInfo, folder: Path):
        self.info = info
        self.folder = folder

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def pdf_path(self) -> Path:
        return self.folder / "source.pdf"

    @property
    def pages_dir(self) -> Path:
        return self.folder / "pages"

    @property
    def descriptions_dir(self) -> Path:
        return self.folder / "descriptions"

    def extraction_dir(self, spec_name: str) -> Path:
        return self.folder / "extractions" / spec_name

    def list_extractions(self) -> list[str]:
        base = self.folder / "extractions"
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []

    def save(self) -> None:
        text = yaml.safe_dump(self.info.model_dump(), allow_unicode=True, sort_keys=False)
        atomic_write_text(self.folder / "document.yaml", text)


class DocumentStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    @property
    def root(self) -> Path:
        return self.workspace.documents_dir

    # --- lettura ---------------------------------------------------------------
    def list(self) -> list[Document]:
        if not self.root.exists():
            return []
        docs = []
        for meta in sorted(self.root.glob("*/document.yaml")):
            docs.append(self._load(meta.parent))
        return docs

    def _load(self, folder: Path) -> Document:
        data = yaml.safe_load((folder / "document.yaml").read_text(encoding="utf-8"))
        return Document(DocumentInfo.model_validate(data), folder)

    def get(self, ref: str) -> Document:
        """Trova un documento per id, per nome del file PDF o per inizio dell'id.

        Così l'utente può scrivere ``kubota`` invece dell'id completo, purché
        non sia ambiguo.
        """
        docs = self.list()
        if not docs:
            raise DocumentError("Il workspace non contiene documenti. Aggiungine uno con 'ragpdf add'.")

        ref_norm = ref.strip()
        for d in docs:
            if ref_norm in (d.id, d.info.source_filename) or slugify(ref_norm) == d.id:
                return d

        matches = [d for d in docs if d.id.startswith(slugify(ref_norm)) or ref_norm.lower() in d.id]
        if len(matches) == 1:
            return matches[0]

        available = "\n".join(f"  - {d.id}" for d in docs)
        if matches:
            raise DocumentError(f"'{ref}' corrisponde a più documenti, specifica meglio:\n{available}")
        raise DocumentError(f"Documento '{ref}' non trovato. Documenti disponibili:\n{available}")

    def find_by_hash(self, sha256: str) -> Document | None:
        return next((d for d in self.list() if d.info.sha256 == sha256), None)

    # --- scrittura -------------------------------------------------------------
    def add(self, pdf_path: Path, primer: str | None = None, doc_id: str | None = None) -> Document:
        pdf_path = Path(pdf_path)
        if not pdf_path.is_file():
            raise DocumentError(f"File non trovato: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise DocumentError(f"Il file non è un PDF: {pdf_path.name}")

        sha = file_sha256(pdf_path)
        existing = self.find_by_hash(sha)
        if existing:
            raise DuplicateDocumentError(existing.info)

        try:
            with pymupdf.open(pdf_path) as pdf:
                page_count = len(pdf)
        except Exception as e:
            raise DocumentError(f"Impossibile aprire il PDF {pdf_path.name}: {e}") from e

        new_id = self._unique_id(slugify(doc_id or pdf_path.stem))
        folder = self.root / new_id
        folder.mkdir(parents=True)

        info = DocumentInfo(
            id=new_id,
            title=pdf_path.stem,
            source_filename=pdf_path.name,
            sha256=sha,
            page_count=page_count,
            primer=primer,
            added_at=utc_now(),
        )
        doc = Document(info, folder)
        shutil.copy2(pdf_path, doc.pdf_path)
        doc.save()
        return doc

    def _unique_id(self, base: str) -> str:
        candidate, n = base, 2
        while (self.root / candidate).exists():
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    def remove(self, doc: Document) -> None:
        shutil.rmtree(doc.folder)
