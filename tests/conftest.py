"""Fixture condivise: workspace temporaneo, PDF di prova, configurazione."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from ragpdf.core.config import Config
from ragpdf.core.documents import DocumentStore
from ragpdf.core.workspace import Workspace


def make_pdf(path: Path, pages: int = 3, prefix: str = "Pagina") -> Path:
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"{prefix} {i}")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config(workspace_dir=str(tmp_path / "data"))
    cfg.render.dpi = 72  # immagini piccole: test più veloci
    return cfg


@pytest.fixture
def workspace(config: Config) -> Workspace:
    return Workspace(config.workspace_path).ensure()


@pytest.fixture
def store(workspace: Workspace) -> DocumentStore:
    return DocumentStore(workspace)


@pytest.fixture
def pdf_factory(tmp_path: Path):
    def factory(name: str = "Catalogo Prova.pdf", pages: int = 3, prefix: str = "Pagina") -> Path:
        return make_pdf(tmp_path / name, pages, prefix)

    return factory
