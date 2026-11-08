"""Strato applicativo: collega configurazione, workspace, documenti e Gemini.

La CLI usa solo questa classe: così la logica non dipende dall'interfaccia, e
domani un'interfaccia diversa (es. web) potrà riutilizzarla così com'è.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ragpdf.core.config import Config, load_config
from ragpdf.core.documents import Document, DocumentStore
from ragpdf.core.gemini import GeminiClient, LLMClient
from ragpdf.core.manifest import Manifest
from ragpdf.core.workspace import Workspace
from ragpdf.indexing.assemble import AssembledIndex, assemble, merge
from ragpdf.indexing.export import write_index
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


class App:
    def __init__(self, config: Config | None = None, client: LLMClient | None = None):
        self.config = config or load_config()
        self.workspace = Workspace(self.config.workspace_path).ensure()
        self.documents = DocumentStore(self.workspace)
        self._client = client

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
                self._client = GeminiClient.from_env(self.config.gemini)
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

    def export(self, doc: Document, spec: IndexingSpec) -> ExportResult:
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
        csv_path = self.workspace.exports_dir / spec.name / f"{doc.id}.csv"
        anomalies = write_index(index, spec, csv_path)
        return ExportResult(csv_path, anomalies, index)

    def export_merged(self, spec: IndexingSpec, docs: list[Document]) -> ExportResult:
        parts = [self.export(d, spec).index for d in docs]
        merged = merge(spec, parts)
        csv_path = self.workspace.exports_dir / spec.name / "TUTTI.csv"
        write_index(merged, spec, csv_path)
        return ExportResult(csv_path, None, merged)

    def indexed_documents(self, spec_name: str) -> list[Document]:
        return [d for d in self.documents.list() if (d.extraction_dir(spec_name) / "manifest.json").exists()]
