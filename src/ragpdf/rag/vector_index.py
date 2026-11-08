"""Indice vettoriale (ChromaDB) della modalità RAG.

Ogni pagina descritta diventa un elemento dell'indice con id
``<id documento>:<numero pagina>``. Si usa ``upsert``: se una pagina viene
rigenerata, la nuova descrizione sostituisce quella vecchia invece di essere
ignorata.

Nota: per ora si usa il modello di embedding di default di ChromaDB; il
passaggio a un modello multilingue locale è previsto nella roadmap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class VectorIndex:
    def __init__(self, path: Path, collection_name: str):
        import chromadb
        from chromadb.config import Settings

        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(path), settings=Settings(allow_reset=True, anonymized_telemetry=False)
        )
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def upsert(self, ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
        if ids:
            self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    def delete_document(self, doc_id: str) -> None:
        self.collection.delete(where={"doc_id": doc_id})

    def query(self, text: str, n_results: int, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        res = self.collection.query(query_texts=[text], n_results=n_results, where=where)
        hits = []
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True):
            hits.append({"text": doc, "metadata": meta, "distance": dist})
        return hits

    def count(self) -> int:
        return self.collection.count()
