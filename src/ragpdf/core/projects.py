"""Progetti di indicizzazione: più PDF con la stessa struttura, un solo CSV.

Un progetto raggruppa dei documenti del workspace che condividono **un primer e una
specifica**. L'indicizzazione elabora tutti i documenti e produce un CSV unico (con la
colonna del file d'origine), più un CSV per ciascun documento.

Ogni documento può avere delle **impostazioni proprie**, facoltative:

- ``notes``: istruzioni in più valide solo per quel PDF (es. "in questo catalogo la
  versione è scritta sotto il nome del motore"); vengono aggiunte alle istruzioni della
  specifica solo per quel documento;
- ``pages``: le pagine da indicizzare, se diverse da quelle della specifica.

Le colonne restano le stesse per tutti i documenti: è la condizione per poter avere un
CSV unico.

Struttura::

    data/projects/<id>/
    ├── project.yaml     nome, documenti, primer, specifica, impostazioni per documento
    └── setup/           sessione di configurazione guidata del progetto

Un singolo PDF è semplicemente un progetto con un documento: i cataloghi aggiunti prima
dell'introduzione dei progetti vengono convertiti automaticamente (vedi ``App``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ragpdf.core.fileio import atomic_write_text, utc_now
from ragpdf.core.workspace import Workspace, slugify


class DocumentSettings(BaseModel):
    notes: str = ""
    pages: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.notes.strip() and not self.pages


class ProjectInfo(BaseModel):
    id: str
    name: str
    documents: list[str] = Field(default_factory=list)
    primer: str | None = None
    spec: str | None = None
    settings: dict[str, DocumentSettings] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class ProjectError(Exception):
    """Errore sui progetti, con messaggio per l'utente."""


class Project:
    def __init__(self, info: ProjectInfo, folder: Path):
        self.info = info
        self.folder = folder

    @property
    def id(self) -> str:
        return self.info.id

    def settings_for(self, doc_id: str) -> DocumentSettings:
        return self.info.settings.get(doc_id) or DocumentSettings()

    def set_settings(self, doc_id: str, settings: DocumentSettings) -> None:
        if settings.is_empty:
            self.info.settings.pop(doc_id, None)
        else:
            self.info.settings[doc_id] = settings

    def save(self) -> None:
        data = self.info.model_dump()
        data["settings"] = {k: v.model_dump() for k, v in self.info.settings.items()}
        atomic_write_text(self.folder / "project.yaml",
                          yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


class ProjectStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    @property
    def root(self) -> Path:
        return self.workspace.root / "projects"

    def list(self) -> list[Project]:
        if not self.root.exists():
            return []
        return [self._load(p.parent) for p in sorted(self.root.glob("*/project.yaml"))]

    def _load(self, folder: Path) -> Project:
        data = yaml.safe_load((folder / "project.yaml").read_text(encoding="utf-8")) or {}
        return Project(ProjectInfo.model_validate(data), folder)

    def get(self, ref: str) -> Project:
        projects = self.list()
        for p in projects:
            if ref in (p.id, p.info.name) or slugify(ref) == p.id:
                return p
        matches = [p for p in projects if slugify(ref) in p.id]
        if len(matches) == 1:
            return matches[0]
        available = "\n".join(f"  - {p.id} ({p.info.name})" for p in projects) or "  (nessuno)"
        raise ProjectError(f"Progetto '{ref}' non trovato. Progetti disponibili:\n{available}")

    def find(self, ref: str) -> Project | None:
        try:
            return self.get(ref)
        except ProjectError:
            return None

    def containing(self, doc_id: str) -> list[Project]:
        return [p for p in self.list() if doc_id in p.info.documents]

    def create(self, name: str, documents: list[str] | None = None, project_id: str | None = None,
               primer: str | None = None, spec: str | None = None) -> Project:
        base = slugify(project_id or name)
        candidate, n = base, 2
        while (self.root / candidate).exists():
            candidate, n = f"{base}-{n}", n + 1
        folder = self.root / candidate
        folder.mkdir(parents=True)
        project = Project(ProjectInfo(id=candidate, name=name.strip() or candidate,
                                      documents=list(documents or []), primer=primer, spec=spec), folder)
        project.save()
        return project

    def add_document(self, project: Project, doc_id: str) -> None:
        if doc_id not in project.info.documents:
            project.info.documents.append(doc_id)
            project.save()

    def remove_document(self, project: Project, doc_id: str) -> None:
        if doc_id in project.info.documents:
            project.info.documents.remove(doc_id)
            project.info.settings.pop(doc_id, None)
            project.save()

    def remove(self, project: Project) -> None:
        shutil.rmtree(project.folder)
