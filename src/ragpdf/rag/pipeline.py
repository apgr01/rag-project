"""Modalità RAG: descrizione delle pagine, costruzione dell'indice, risposte.

Le tre fasi sono separate e rilanciabili singolarmente:

1. :func:`describe_document` — Gemini descrive ogni pagina (immagine + primer);
   le descrizioni vengono salvate in ``descriptions/`` del documento.
2. :func:`build_index` — le descrizioni salvate entrano nell'indice vettoriale.
   Non chiama Gemini: si può rilanciare quante volte si vuole.
3. :class:`QueryEngine` — recupera le pagine pertinenti e genera la risposta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ragpdf.core.config import Config
from ragpdf.core.documents import Document
from ragpdf.core.fileio import read_json, utc_now, write_json
from ragpdf.core.gemini import LLMClient
from ragpdf.core.manifest import Manifest
from ragpdf.core.page_runner import RunReport, run_pages
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import render_pages
from ragpdf.rag.vector_index import VectorIndex


def describe_document(
    doc: Document,
    primer_text: str,
    client: LLMClient,
    config: Config,
    force: bool = False,
    show_progress: bool = True,
) -> RunReport:
    out_dir = doc.descriptions_dir
    manifest = Manifest.load_or_create(out_dir / "manifest.json", doc.info.page_count)
    if force:
        manifest.reset()

    todo = manifest.pages_to_process(config.indexing.max_page_attempts)
    if not todo:
        return RunReport()

    images = render_pages(doc.pdf_path, doc.pages_dir, config.render.dpi, todo, show_progress=show_progress)
    template = load_prompt("rag_describe_page")

    def worker(n: int) -> None:
        prompt = render_prompt(
            template, primer=primer_text, page_number=n, total_pages=doc.info.page_count
        )
        text = client.generate_text(prompt, images=[images[n]])
        write_json(
            out_dir / f"page-{n:04d}.json",
            {"page": n, "model": config.gemini.model, "generated_at": utc_now(), "description": text},
        )

    parallelism = config.indexing.max_workers or client.parallelism
    return run_pages(todo, worker, manifest, parallelism, "Descrizione pagine", show_progress)


def build_index(docs: list[Document], config: Config, index: VectorIndex) -> int:
    """Inserisce nell'indice tutte le descrizioni disponibili. Restituisce il numero di pagine."""
    total = 0
    for doc in docs:
        ids, texts, metas = [], [], []
        for path in sorted(doc.descriptions_dir.glob("page-*.json")):
            data = read_json(path)
            if not data.get("description"):
                continue
            ids.append(f"{doc.id}:{data['page']}")
            texts.append(data["description"])
            metas.append({"doc_id": doc.id, "source": doc.info.source_filename, "page": data["page"]})
        index.upsert(ids, texts, metas)
        total += len(ids)
    return total


@dataclass
class Answer:
    question: str
    text: str
    sources: list[dict[str, Any]]


class QueryEngine:
    def __init__(self, index: VectorIndex, client: LLMClient, config: Config):
        self.index = index
        self.client = client
        self.config = config

    def ask(self, question: str, doc_id: str | None = None) -> Answer:
        if not question.strip():
            raise ValueError("La domanda è vuota.")
        where = {"doc_id": doc_id} if doc_id else None
        hits = self.index.query(question, self.config.rag.top_k, where)
        if not hits:
            return Answer(question, "Non ho trovato informazioni pertinenti nei documenti indicizzati.", [])

        blocks, sources = [], []
        for i, hit in enumerate(hits, start=1):
            meta = hit["metadata"]
            blocks.append(f"[FONTE {i}: {meta['source']} — pagina {meta['page']}]\n{hit['text']}")
            sources.append({"source": meta["source"], "page": meta["page"], "distance": hit["distance"]})

        prompt = render_prompt(load_prompt("rag_answer"), context="\n\n---\n\n".join(blocks), question=question)
        return Answer(question, self.client.generate_text(prompt), sources)
