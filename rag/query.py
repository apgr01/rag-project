"""
query.py

Interroga l'indice costruito da build_index.py:
1. Retrieval: cerca nel database Chroma i chunk (descrizioni di pagina)
   piu' semanticamente simili alla domanda dell'utente.
2. Generazione: passa quei chunk come contesto a Gemini, che scrive la
   risposta finale in linguaggio naturale, citando le pagine di origine.

Setup:
    pip install chromadb sentence-transformers google-genai python-dotenv
    # .env nella root del progetto: GEMINI_API_KEY=la_tua_chiave

Uso:
    python query.py "a che pagina si trova il codice FIN75061OF?"
    python query.py "come sono ordinati i motori nel catalogo?" --top-k 8
    python query.py "cosa c'e' nella pagina di indice?" --source "Catalogo 5 KUBOTA rel 1010.pdf"
"""

import argparse
import sys
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))  # cosi' troviamo il pacchetto common/ dalla root

from common.gemini_client import KeyManager, QuotaExhausted, generate_with_rotation  # noqa: E402

STORE_DIR = SCRIPT_DIR / "store"

COLLECTION_NAME = "catalogo_pagine"

# Deve essere lo STESSO modello usato in build_index.py: cambiarlo qui senza
# ricostruire l'indice produce risultati di ricerca senza senso.
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

GENERATION_MODEL = "gemini-3.8-flash"

DEFAULT_TOP_K = 5

SYSTEM_PROMPT = """Sei un assistente che risponde a domande su un catalogo ricambi per motori, \
basandoti ESCLUSIVAMENTE sugli estratti forniti qui sotto. Ogni estratto e' preceduto \
dal nome del catalogo e dal numero di pagina del FILE PDF (che puo' NON corrispondere \
alla numerazione stampata dentro il catalogo).

Regole:
- Rispondi solo usando le informazioni presenti negli estratti forniti. Se la risposta \
non si trova negli estratti, dillo chiaramente invece di inventare.
- Quando indichi una pagina, specifica sempre che si tratta della "pagina N del file PDF", \
non della numerazione interna del catalogo (che potrebbe essere diversa).
- Se piu' estratti riguardano la stessa domanda (es. lo stesso codice componente presente \
su piu' motori), elencali tutti.
- Rispondi in italiano, in modo diretto e conciso.
"""


def get_collection():
    client = chromadb.PersistentClient(path=str(STORE_DIR))
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL_NAME
    )
    try:
        return client.get_collection(name=COLLECTION_NAME, embedding_function=embedding_fn)
    except Exception as e:
        raise RuntimeError(
            f"Impossibile aprire la collezione '{COLLECTION_NAME}' in {STORE_DIR}. "
            f"Hai eseguito build_index.py? Dettaglio: {e}"
        )


def retrieve(collection, query: str, top_k: int, source_filter: str | None = None):
    where = {"source_pdf": source_filter} if source_filter else None

    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where=where,
    )

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    return list(zip(documents, metadatas, distances))


def build_context(retrieved) -> str:
    blocks = []
    for document, metadata, _distance in retrieved:
        blocks.append(
            f"--- {metadata['source_pdf']} — pagina PDF {metadata['pdf_page_number']} ---\n{document}"
        )
    return "\n\n".join(blocks)


def generate_answer(key_manager: KeyManager, query: str, context: str) -> str:
    prompt = f"{SYSTEM_PROMPT}\n\nESTRATTI DAL CATALOGO:\n{context}\n\nDOMANDA DELL'UTENTE:\n{query}"

    response = generate_with_rotation(
        key_manager,
        model=GENERATION_MODEL,
        contents=[prompt],
    )

    if not response.text or not response.text.strip():
        raise ValueError("Risposta vuota da Gemini.")

    return response.text.strip()


def answer_query(query: str, top_k: int = DEFAULT_TOP_K, source_filter: str | None = None, show_sources: bool = True) -> str:
    collection = get_collection()
    retrieved = retrieve(collection, query, top_k, source_filter)

    if not retrieved:
        return "Non ho trovato nulla di rilevante nell'indice per questa domanda."

    context = build_context(retrieved)

    key_manager = KeyManager()
    answer = generate_answer(key_manager, query, context)

    if show_sources:
        sources_lines = [
            f"  - {m['source_pdf']}, pagina PDF {m['pdf_page_number']} (distanza: {d:.3f})"
            for _doc, m, d in retrieved
        ]
        answer += "\n\nFonti consultate:\n" + "\n".join(sources_lines)

    return answer


def parse_args():
    parser = argparse.ArgumentParser(description="Interroga il catalogo tramite RAG.")
    parser.add_argument("query", help="La domanda da porre.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help=f"Numero di chunk da recuperare (default: {DEFAULT_TOP_K}).")
    parser.add_argument("--source", default=None, help="Filtra solo su un catalogo specifico (nome file PDF esatto).")
    parser.add_argument("--no-sources", action="store_true", help="Non mostrare l'elenco delle fonti in fondo alla risposta.")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        answer = answer_query(
            query=args.query,
            top_k=args.top_k,
            source_filter=args.source,
            show_sources=not args.no_sources,
        )
    except (RuntimeError, QuotaExhausted) as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(answer)


if __name__ == "__main__":
    main()