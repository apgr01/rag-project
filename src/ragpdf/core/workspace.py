"""Il *workspace*: la cartella con tutti i dati dell'utente.

Struttura::

    data/
    ├── projects/<id>/       progetti: gruppi di PDF con stesso primer e specifica (projects.py)
    ├── documents/<id>/      un documento: PDF, pagine, risultati (vedi documents.py)
    ├── primers/<nome>.md    primer, condivisi tra documenti dello stesso tipo
    ├── specs/<nome>.yaml    specifiche di indicizzazione (cosa estrarre e come)
    ├── exports/             CSV prodotti, pronti da usare
    └── vector_index/        database vettoriale della modalità RAG

Tutto ciò che appartiene all'utente sta qui: per un backup basta copiare questa
cartella. L'applicazione (codice, configurazione, prompt) resta separata.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def slugify(text: str, max_length: int = 60) -> str:
    """Trasforma un testo in un identificatore leggibile e sicuro come nome di file.

    >>> slugify("Catalogo 5 KUBOTA rel 1010.pdf")
    'catalogo-5-kubota-rel-1010-pdf'
    """
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug[:max_length].rstrip("-") or "documento"


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)

    # --- cartelle principali -------------------------------------------------
    @property
    def documents_dir(self) -> Path:
        return self.root / "documents"

    @property
    def primers_dir(self) -> Path:
        return self.root / "primers"

    @property
    def specs_dir(self) -> Path:
        return self.root / "specs"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def vector_index_dir(self) -> Path:
        return self.root / "vector_index"

    def ensure(self) -> Workspace:
        for d in (self.documents_dir, self.primers_dir, self.specs_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    # --- primer --------------------------------------------------------------
    def primer_path(self, name: str) -> Path:
        return self.primers_dir / f"{name}.md"

    def list_primers(self) -> list[str]:
        return sorted(p.stem for p in self.primers_dir.glob("*.md"))

    def read_primer(self, name: str) -> str:
        path = self.primer_path(name)
        if not path.exists():
            raise FileNotFoundError(f"Primer '{name}' non trovato ({path}).")
        return path.read_text(encoding="utf-8")

    # --- specifiche ----------------------------------------------------------
    def spec_path(self, name: str) -> Path:
        return self.specs_dir / f"{name}.yaml"

    def list_specs(self) -> list[str]:
        return sorted(p.stem for p in self.specs_dir.glob("*.yaml"))
