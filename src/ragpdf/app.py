"""Strato applicativo: collega configurazione, workspace, documenti e Gemini.

La CLI usa solo questa classe: così la logica non dipende dall'interfaccia, e
domani un'interfaccia diversa (es. web) potrà riutilizzarla così com'è.
"""

from __future__ import annotations

import os
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ragpdf.core.config import Config, load_config
from ragpdf.core.documents import Document, DocumentStore
from ragpdf.core.gemini import GeminiClient, LLMClient
from ragpdf.core.library import Library, LibraryCard, LibraryError
from ragpdf.core.manifest import Manifest
from ragpdf.core.projects import DocumentSettings, Project, ProjectStore
from ragpdf.core.render import format_pages
from ragpdf.core.workspace import Workspace
from ragpdf.indexing.assemble import AssembledIndex, assemble, merge
from ragpdf.indexing.export import write_anomalies_by_document, write_index
from ragpdf.indexing.extractor import Extractor, load_page_results, selected_pages
from ragpdf.indexing.spec import IndexingSpec, SpecError, load_spec

FAKE_ENV_VAR = "RAGPDF_FAKE_GEMINI"


class PrimerMissingError(Exception):
    pass


@dataclass
class ExportResult:
    csv_path: Path
    anomalies_path: Path | None
    index: AssembledIndex
    stale: bool = False   # specifica/primer cambiati dopo l'estrazione


@dataclass
class ProjectExport:
    csv_path: Path
    anomalies_path: Path | None
    index: AssembledIndex                                   # CSV unico
    documents: list[tuple[Document, ExportResult]] = field(default_factory=list)
    not_indexed: list[Document] = field(default_factory=list)
    search_page: Path | None = None

    @property
    def stale(self) -> list[Document]:
        return [d for d, r in self.documents if r.stale]


class App:
    def __init__(self, config: Config | None = None, client: LLMClient | None = None):
        self.config = config or load_config()
        self.workspace = Workspace(self.config.workspace_path).ensure()
        self.documents = DocumentStore(self.workspace)
        self.projects = ProjectStore(self.workspace)
        self.library = Library(self.workspace, self.documents)
        self._client = client
        self.events: deque[dict] = deque(maxlen=300)
        self.ensure_projects()

    # --- messaggi per l'utente ----------------------------------------------------------
    def emit(self, message: str) -> None:
        """Messaggio di servizio (sovraccarico, quote, ...): nel terminale e per l'interfaccia web."""
        from tqdm import tqdm

        tqdm.write(message)
        self.events.append({"ts": time.time(), "text": message})

    def events_since(self, ts: float, limit: int = 15) -> list[dict]:
        return [e for e in self.events if e["ts"] >= ts][-limit:]

    # --- progetti -------------------------------------------------------------------
    def ensure_projects(self) -> None:
        """Aggiorna i dati delle versioni precedenti.

        - I documenti aggiunti prima dei progetti (senza progetto e senza scheda della
          biblioteca) diventano progetti con un solo PDF, con lo stesso id, primer e
          specifica; la loro sessione di configurazione guidata viene spostata nel progetto.
        - Ogni documento ottiene la sua scheda della biblioteca (nella cartella principale):
          da qui in poi i PDF vivono nella biblioteca e possono anche non stare in un progetto.
        """
        known = {d for p in self.projects.list() for d in p.info.documents}
        for doc in self.documents.list():
            if self.library.has_card(doc):
                continue
            self.library.save_card(doc, LibraryCard())
            if doc.id in known:
                continue
            project = self.projects.create(doc.info.title, [doc.id], project_id=doc.id,
                                           primer=doc.info.primer, spec=doc.info.spec)
            old_setup = doc.folder / "setup"
            if old_setup.exists() and not (project.folder / "setup").exists():
                shutil.move(str(old_setup), str(project.folder / "setup"))

    def add_document(self, pdf_path: Path, project: Project | None = None, doc_id: str | None = None) -> Document:
        """Aggiunge un PDF alla biblioteca e a un progetto; senza progetto ne crea uno con
        solo quel PDF (comportamento della CLI)."""
        doc = self.documents.add(Path(pdf_path), doc_id=doc_id)
        self.library.save_card(doc, LibraryCard())
        if project is None:
            self.projects.create(doc.info.title, [doc.id], project_id=doc.id)
        else:
            self.projects.add_document(project, doc.id)
        return doc

    def add_to_library(self, pdf_path: Path, folder: str = "") -> Document:
        """Aggiunge un PDF alla biblioteca, nella cartella indicata, senza progetto."""
        doc = self.documents.add(Path(pdf_path))
        self.library.save_card(doc, LibraryCard(folder=folder))
        return doc

    def remove_from_project(self, project: Project, doc: Document) -> None:
        """Toglie un documento dal progetto: il PDF resta nella biblioteca."""
        self.projects.remove_document(project, doc.id)

    def remove_project(self, project: Project) -> None:
        """Elimina il progetto; i suoi PDF (e i risultati) restano nella biblioteca."""
        self.projects.remove(project)

    def delete_document(self, doc: Document) -> None:
        """Elimina un PDF dalla biblioteca, con pagine e risultati; solo se nessun progetto lo usa."""
        using = self.projects.containing(doc.id)
        if using:
            names = ", ".join(f"'{p.info.name}'" for p in using)
            raise LibraryError(f"'{doc.info.title}' è usato dai progetti {names}: toglilo prima dai progetti.")
        self.documents.remove(doc)

    def create_project_from_library(self, name: str, docs: list[Document]) -> Project:
        """Nuovo progetto con i cataloghi scelti nella biblioteca.

        Le collezioni "da indicizzare" diventano le pagine da indicizzare di ciascun PDF.
        """
        project = self.projects.create(name, [d.id for d in docs])
        for doc in docs:
            pages = self.library.card(doc).pages_with_tag("da-indicizzare")
            if pages:
                project.set_settings(doc.id, DocumentSettings(pages=format_pages(pages)))
        project.save()
        return project

    def example_page_refs(self, docs: list[Document], limit: int) -> list[tuple[str, int]]:
        """Pagine segnate come "pagina d'esempio" nella biblioteca, al massimo ``limit``."""
        refs = [(d.id, p) for d in docs for p in self.library.card(d).pages_with_tag("esempio")]
        return refs[:limit]

    def project_documents(self, project: Project) -> list[Document]:
        docs = []
        for doc_id in project.info.documents:
            try:
                docs.append(self.documents.get(doc_id))
            except Exception:  # documento eliminato: lo si ignora
                continue
        return docs

    def project_primer(self, project: Project) -> str:
        return self.workspace.read_primer(project.info.primer) if project.info.primer else ""

    def project_spec(self, project: Project) -> IndexingSpec:
        if not project.info.spec:
            raise SpecError(
                f"Il progetto '{project.info.name}' non ha ancora una specifica: completa prima la configurazione."
            )
        return self.load_spec(project.info.spec)

    def document_spec(self, project: Project, doc: Document, base: IndexingSpec | None = None) -> IndexingSpec:
        """La specifica del progetto con note e pagine proprie del documento."""
        base = base or self.project_spec(project)
        settings = project.settings_for(doc.id)
        return base.for_document(settings.notes, settings.pages, doc.info.title)

    def apply_document_settings(self, doc: Document, spec: IndexingSpec) -> IndexingSpec:
        """Applica le impostazioni del documento se fa parte di un progetto con questa specifica."""
        for project in self.projects.containing(doc.id):
            if project.info.spec == spec.name:
                return self.document_spec(project, doc, spec)
        return spec

    def assign_project_rules(self, project: Project, primer: str, spec: str) -> None:
        project.info.primer, project.info.spec = primer, spec
        project.save()
        for doc in self.project_documents(project):
            doc.info.primer, doc.info.spec = primer, spec
            doc.save()

    def project_export_dir(self, project: Project) -> Path:
        return self.workspace.exports_dir / project.id

    def export_project(self, project: Project) -> ProjectExport:
        """CSV unico del progetto + un CSV per documento (senza chiamare Gemini)."""
        base = self.project_spec(project)
        primer = self.project_primer(project)
        out_dir = self.project_export_dir(project)
        parts: list[tuple[Document, ExportResult]] = []
        missing: list[Document] = []
        for doc in self.project_documents(project):
            spec = self.document_spec(project, doc, base)
            if not (doc.extraction_dir(spec.name) / "manifest.json").exists():
                missing.append(doc)
                continue
            parts.append((doc, self.export(doc, spec, target_dir=out_dir / "documenti", primer=primer)))
        merged = merge(base, [r.index for _, r in parts], deduplicate=False)
        merged.stats.update(
            documenti_indicizzati=len(parts),
            documenti_totali=len(parts) + len(missing),
            errori=sum(r.index.stats.get("errori", 0) for _, r in parts),
            avvisi=sum(r.index.stats.get("avvisi", 0) for _, r in parts),
            pagine_da_elaborare=sum(r.index.stats.get("pagine_da_elaborare", 0) for _, r in parts),
        )
        csv_path = out_dir / f"{project.id}.csv"
        write_index(merged, base, csv_path)
        anomalies = write_anomalies_by_document(
            [(d.info.source_filename, r.index) for d, r in parts], base, out_dir / f"{project.id}_anomalie.csv"
        )
        result = ProjectExport(csv_path, anomalies, merged, parts, missing)
        if parts:
            from ragpdf.indexing.search_page import build_search_page

            result.search_page = build_search_page(self, project, result)
        return result

    # --- Gemini ---------------------------------------------------------------------
    @property
    def simulated(self) -> bool:
        return os.getenv(FAKE_ENV_VAR, "").strip().lower() in ("1", "true", "si", "sì", "yes")

    def client(self) -> LLMClient:
        """Il client viene creato solo quando serve: i comandi che non chiamano
        Gemini (list, export, ...) funzionano anche senza chiavi API."""
        if self._client is None:
            if self.simulated:
                from ragpdf.core.fake_gemini import FakeGeminiClient

                self._client = FakeGeminiClient()
            else:
                self._client = GeminiClient.from_env(self.config.gemini, log=self.emit)
        return self._client

    # --- primer ---------------------------------------------------------------------
    def primer_text(self, doc: Document) -> str:
        if not doc.info.primer:
            raise PrimerMissingError(f"Il documento '{doc.id}' non ha un primer associato.")
        return self.workspace.read_primer(doc.info.primer)

    def assign_primer(self, doc: Document, name: str) -> None:
        if not self.workspace.primer_path(name).exists():
            raise FileNotFoundError(f"Primer '{name}' non trovato in {self.workspace.primers_dir}.")
        doc.info.primer = name
        doc.save()

    def assign_spec(self, doc: Document, name: str) -> None:
        doc.info.spec = name
        doc.save()

    # --- specifiche -----------------------------------------------------------------
    def load_spec(self, ref: str) -> IndexingSpec:
        """Carica una specifica per nome (``data/specs/<nome>.yaml``) o per percorso."""
        as_path = Path(ref)
        if as_path.suffix.lower() in (".yaml", ".yml") and as_path.exists():
            return load_spec(as_path)
        path = self.workspace.spec_path(ref)
        if not path.exists():
            available = self.workspace.list_specs()
            hint = (
                "Specifiche disponibili: " + ", ".join(available)
                if available
                else "Non ci sono ancora specifiche: creane una con 'ragpdf spec new <nome>'."
            )
            raise SpecError(f"Specifica '{ref}' non trovata.\n{hint}")
        return load_spec(path)

    # --- indicizzazione --------------------------------------------------------------
    def extractor(self) -> Extractor:
        return Extractor(self.client(), self.config)

    def export(
        self, doc: Document, spec: IndexingSpec, target_dir: Path | None = None, primer: str | None = None
    ) -> ExportResult:
        """Rigenera il CSV di un documento dai risultati salvati (senza chiamare Gemini)."""
        out_dir = doc.extraction_dir(spec.name)
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Il documento '{doc.id}' non è ancora stato indicizzato con la specifica '{spec.name}'."
            )
        manifest = Manifest.load_or_create(manifest_path, doc.info.page_count)
        results = load_page_results(doc, spec.name)
        expected = sorted(set(selected_pages(doc, spec)) | set(results))
        index = assemble(spec, results, doc.info.source_filename, expected, manifest.failed_pages())
        csv_path = (target_dir or self.workspace.exports_dir / spec.name) / f"{doc.id}.csv"
        anomalies = write_index(index, spec, csv_path)
        return ExportResult(csv_path, anomalies, index, stale=self._is_stale(doc, spec, manifest, primer))

    def _is_stale(self, doc: Document, spec: IndexingSpec, manifest: Manifest, primer: str | None = None) -> bool:
        if primer is None:
            try:
                primer = self.primer_text(doc) if doc.info.primer else ""
            except FileNotFoundError:
                return False
        current = spec.extraction_fingerprint(primer, self.config.gemini.model, self.config.indexing.input)
        return manifest.meta.get("fingerprint") not in (None, current)

    def export_merged(
        self,
        spec: IndexingSpec,
        docs: list[Document],
        target_dir: Path | None = None,
        specs: dict[str, IndexingSpec] | None = None,
    ) -> ExportResult:
        """Unisce i CSV di più documenti. Le specifiche devono avere le stesse colonne."""
        specs = specs or {}
        parts = [self.export(d, specs.get(d.id, spec)).index for d in docs]
        merged = merge(spec, parts)
        csv_path = (target_dir or self.workspace.exports_dir / spec.name) / "TUTTI.csv"
        write_index(merged, spec, csv_path)
        return ExportResult(csv_path, None, merged)

    def indexed_documents(self, spec_name: str) -> list[Document]:
        return [d for d in self.documents.list() if (d.extraction_dir(spec_name) / "manifest.json").exists()]
