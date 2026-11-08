"""Estrazione: Gemini legge ogni pagina e restituisce le righe dell'indice.

Il risultato di ogni pagina è salvato in
``data/documents/<id>/extractions/<specifica>/page-NNNN.json``::

    {
      "page": 12,
      "page_summary": "Scheda tecnica del motore 3LD 450/S, prima pagina",
      "page_context": {"MOTORE": "3LD 450/S", ...},
      "rows": [{"COSTRUTTORE": "LOMBARDINI", "MOTORE": "3LD 450/S", ...}, ...],
      "notes": "",
      "model": "...",
      "generated_at": "..."
    }

I risultati restano su disco: cambiare le regole di output, unire più
documenti o rigenerare il CSV non richiede nuove chiamate a Gemini.

Il manifest memorizza l'*impronta* della specifica (vedi
``IndexingSpec.extraction_fingerprint``). Se la specifica o il primer cambiano
dopo un'estrazione, il programma se ne accorge e chiede se rifarla, invece di
mescolare risultati prodotti con regole diverse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ragpdf.core.config import Config
from ragpdf.core.documents import Document
from ragpdf.core.fileio import read_json, utc_now, write_json
from ragpdf.core.gemini import LLMClient
from ragpdf.core.manifest import DONE, Manifest
from ragpdf.core.page_runner import RunReport, run_pages
from ragpdf.core.render import parse_page_ranges, render_pages
from ragpdf.indexing.prompt import PromptBuilder
from ragpdf.indexing.spec import IndexingSpec


class SpecChangedError(Exception):
    """La specifica o il primer sono cambiati rispetto all'estrazione esistente."""

    def __init__(self, done_pages: int):
        self.done_pages = done_pages
        super().__init__(
            f"La specifica, il primer o il modello sono cambiati dopo l'ultima estrazione "
            f"({done_pages} pagine già elaborate con le regole precedenti)."
        )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def normalize_response(spec: IndexingSpec, response: Any) -> dict[str, Any]:
    """Riporta la risposta di Gemini ai nomi di colonna della specifica.

    Tollera piccole irregolarità (chiavi mancanti, numeri invece di stringhe,
    ``null``), così una risposta quasi corretta non fa fallire la pagina.
    """
    if not isinstance(response, dict):
        raise ValueError(f"risposta inattesa dal modello (tipo {type(response).__name__})")

    keys = spec.json_keys()
    rows_in = response.get("rows") or []
    if not isinstance(rows_in, list):
        raise ValueError("il campo 'rows' della risposta non è un elenco")

    rows = []
    for item in rows_in:
        if not isinstance(item, dict):
            continue
        rows.append({col: _as_text(item.get(key)) for col, key in keys.items()})

    ctx_in = response.get("page_context") or {}
    context = {
        c.name: _as_text(ctx_in.get(keys[c.name])) for c in spec.context_columns
    } if isinstance(ctx_in, dict) else {}

    return {
        "page_summary": _as_text(response.get("page_summary")),
        "page_context": context,
        "rows": rows,
        "notes": _as_text(response.get("notes")),
    }


def selected_pages(doc: Document, spec: IndexingSpec, pages: str | None = None) -> list[int]:
    """Pagine da considerare: quelle indicate da riga di comando, o quelle della specifica."""
    selection = pages if pages is not None else spec.pages
    return parse_page_ranges(selection, doc.info.page_count) or list(
        range(1, doc.info.page_count + 1)
    )


class Extractor:
    def __init__(self, client: LLMClient, config: Config):
        self.client = client
        self.config = config

    @staticmethod
    def result_path(out_dir: Path, page: int) -> Path:
        return out_dir / f"page-{page:04d}.json"

    def open_manifest(
        self, doc: Document, spec: IndexingSpec, primer_text: str, force: bool = False
    ) -> Manifest:
        out_dir = doc.extraction_dir(spec.name)
        fingerprint = spec.extraction_fingerprint(primer_text, self.config.gemini.model)
        meta = {"spec": spec.name, "fingerprint": fingerprint, "model": self.config.gemini.model}
        manifest = Manifest.load_or_create(out_dir / "manifest.json", doc.info.page_count, meta)

        if force:
            for old in out_dir.glob("page-*.json"):
                old.unlink()
            manifest.reset(meta)
        elif manifest.meta.get("fingerprint") != fingerprint:
            done = manifest.counts().get(DONE, 0)
            if done:
                raise SpecChangedError(done)
            manifest.reset(meta)  # nessuna pagina fatta: basta aggiornare l'impronta
        return manifest

    def run(
        self,
        doc: Document,
        spec: IndexingSpec,
        primer_text: str,
        pages: str | None = None,
        force: bool = False,
        show_progress: bool = True,
    ) -> RunReport:
        manifest = self.open_manifest(doc, spec, primer_text, force)
        selected = selected_pages(doc, spec, pages)
        todo = manifest.pages_to_process(self.config.indexing.max_page_attempts, only=selected)
        if not todo:
            return RunReport()

        out_dir = doc.extraction_dir(spec.name)
        images = render_pages(doc.pdf_path, doc.pages_dir, self.config.render.dpi, todo, show_progress=show_progress)
        builder = PromptBuilder(spec, primer_text, doc.info.page_count)

        def worker(n: int) -> None:
            response = self.client.generate_json(builder.page_prompt(n), builder.schema, images=[images[n]])
            result = normalize_response(spec, response)
            write_json(
                self.result_path(out_dir, n),
                {"page": n, **result, "model": self.config.gemini.model, "generated_at": utc_now()},
            )

        parallelism = self.config.indexing.max_workers or self.client.parallelism
        return run_pages(todo, worker, manifest, parallelism, "Indicizzazione", show_progress)


def load_page_results(doc: Document, spec_name: str) -> dict[int, dict[str, Any]]:
    out_dir = doc.extraction_dir(spec_name)
    return {int(p.stem.split("-")[1]): read_json(p) for p in sorted(out_dir.glob("page-*.json"))}
