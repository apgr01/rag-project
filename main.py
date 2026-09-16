import argparse
import sys
from pathlib import Path

from common.config import load_config
from common.gemini_client import GeminiClient
from common.vector_store import VectorStore
from ingestion.standard_ingestor import StandardIngestor
from rag.standard_engine import StandardQueryEngine


def parse_args():
    parser = argparse.ArgumentParser(description="Industrial Catalog RAG CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Comando 'ingest'
    ingest_parser = subparsers.add_parser("ingest", help="Esegue l'ingestion di un file PDF.")
    ingest_parser.add_argument("--file", required=True, help="Percorso del file PDF.")
    ingest_parser.add_argument("--force", action="store_true", help="Forza il re-rendering e la ri-elaborazione.")
    ingest_parser.add_argument("--rpm", type=int, default=10, help="Richieste al minuto massime (default: 10).")

    # Comando 'query'
    query_parser = subparsers.add_parser("query", help="Pone una domanda al sistema RAG.")
    query_parser.add_argument("prompt", type=str, help="La domanda da porre al sistema.")
    query_parser.add_argument("--source", type=str, default=None, help="Nome esatto del PDF sorgente su cui filtrare (es. 'Catalogo 5 KUBOTA rel 1010.pdf').")

    # Comando 'list'
    list_parser = subparsers.add_parser("list", help="Elenca i PDF ingeriti finora.")

    return parser.parse_args()


def main():
    args = parse_args()

    # 1. Inizializzazione dei componenti centrali
    config = load_config()
    gemini_client = GeminiClient()
    vector_store = VectorStore(
        db_path=config.rag.vector_db_path,
        collection_name=config.rag.collection_name
    )

    # 2. Gestione dei comandi da CLI
    if args.command == "ingest":
        ingestor = StandardIngestor(config, gemini_client, vector_store)
        ingestor.process_document(file_path=args.file, force=args.force, rpm=args.rpm)

    elif args.command == "query":
        engine = StandardQueryEngine(config, gemini_client, vector_store)
        result = engine.answer_query(query=args.prompt , source_filter=args.source)

        print("\n" + "=" * 50)
        print(f"❓ Domanda: {result['query']}")
        print("=" * 50)
        print(f"💡 Risposta:\n{result['answer']}")
        print("=" * 50)
        print("📚 Fonti utilizzate:")
        for src in result["sources"]:
            print(f"  • {src['source']} (Pagina {src['page']})")
        print("=" * 50 + "\n")

    elif args.command == "list":
        from common.paths import DESCRIPTIONS_DIR
        import json as _json

        manifests = sorted(DESCRIPTIONS_DIR.glob("*/manifest.json"))
        if not manifests:
            print("Nessun documento ingerito.")
            return

        print("\n" + "=" * 60)
        print("📄 Documenti ingeriti:")
        print("=" * 60)
        for m_path in manifests:
            with open(m_path, "r", encoding="utf-8") as f:
                m = _json.load(f)
            done = sum(1 for p in m["pages"].values() if p["status"] == "done")
            failed = sum(1 for p in m["pages"].values() if p["status"] == "failed")
            print(f"  • {m['source_pdf']}")
            print(f"      pagine: {done}/{m['total_pages']} completate, {failed} fallite")
        print("=" * 60 + "\n")

if __name__ == "__main__":
    main()