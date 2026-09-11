"""
build_index.py

Legge tutte le descrizioni di pagina generate da describe_pages.py
(data/descriptions/<pdf>/page-*.json) e le indicizza in un database
vettoriale Chroma persistente, per poterle poi cercare semanticamente.

Embedding: modello locale multilingue (sentence-transformers), gira su CPU,
nessuna chiamata API, nessun costo, nessun rate limit.

Puoi rilanciare questo script tutte le volte che vuoi (es. dopo aver
aggiunto un nuovo catalogo, o rigenerato alcune descrizioni): usa upsert,
quindi aggiorna i chunk già esistenti invece di duplicarli.

Setup:
    pip install chromadb sentence-transformers

Uso:
    python build_index.py
    python build_index.py --reset   # cancella e ricostruisce l'indice da zero
"""

import argparse
import json
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

SCRIPT_DIR = Path(__file__).resolve().parent
DESCRIPTIONS_DIR = SCRIPT_DIR / "../data/descriptions"
STORE_DIR = SCRIPT_DIR / "store"

COLLECTION_NAME = "catalogo_pagine"

# Modello di embedding multilingue, leggero, adatto a CPU senza GPU.
# Stesso modello va usato in query.py: non e' intercambiabile a piacere,
# cambiarlo richiede ricostruire l'indice da zero (dimensioni diverse).
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def get_collection(reset: bool = False):
    client = chromadb.PersistentClient(path=str(STORE_DIR))

    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL_NAME
    )

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"🗑️  Collezione '{COLLECTION_NAME}' esistente cancellata.")
        except Exception:
            pass  # non esisteva ancora, nessun problema

    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
    )


def load_page_descriptions():
    """Trova tutti i file page-*.json in ogni sottocartella di DESCRIPTIONS_DIR
    (uno per PDF) e li carica come dizionari."""
    if not DESCRIPTIONS_DIR.exists():
        raise FileNotFoundError(
            f"Cartella non trovata: {DESCRIPTIONS_DIR}. "
            f"Esegui prima describe_pages.py."
        )

    records = []
    for pdf_dir in sorted(DESCRIPTIONS_DIR.iterdir()):
        if not pdf_dir.is_dir():
            continue
        for json_path in sorted(pdf_dir.glob("page-*.json")):
            with open(json_path, "r", encoding="utf-8") as f:
                records.append(json.load(f))

    return records


def build_index(reset: bool = False):
    collection = get_collection(reset=reset)
    records = load_page_descriptions()

    if not records:
        print("⚠️  Nessuna descrizione trovata. Hai eseguito describe_pages.py?")
        return

    ids = []
    documents = []
    metadatas = []

    for record in records:
        pdf_stem = Path(record["source_pdf"]).stem
        page_number = record["page_number"]

        chunk_id = f"{pdf_stem}__page_{page_number:04d}"

        # Un po' di contesto esplicito prima della descrizione: aiuta la
        # ricerca semantica quando l'utente nomina il catalogo o un modello
        # specifico nella domanda.
        document_text = f"[Catalogo: {pdf_stem} — pagina PDF {page_number}]\n{record['description']}"

        metadata = {
            "source_pdf": record["source_pdf"],
            "pdf_page_number": page_number,  # numero pagina del FILE PDF, non della numerazione interna del catalogo
            "primer_version": record.get("primer_version", ""),
            "model": record.get("model", ""),
        }

        ids.append(chunk_id)
        documents.append(document_text)
        metadatas.append(metadata)

    # upsert invece di add: se rilanci lo script su descrizioni gia'
    # indicizzate, aggiorna i chunk esistenti (stesso id) invece di
    # duplicarli o fallire con un errore di id duplicato.
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    print(f"✅ Indicizzati {len(ids)} chunk nella collezione '{COLLECTION_NAME}'.")
    print(f"   Totale chunk ora presenti nell'indice: {collection.count()}")


def parse_args():
    parser = argparse.ArgumentParser(description="Costruisce l'indice vettoriale dalle descrizioni di pagina.")
    parser.add_argument("--reset", action="store_true", help="Cancella l'indice esistente e lo ricostruisce da zero.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build_index(reset=args.reset)