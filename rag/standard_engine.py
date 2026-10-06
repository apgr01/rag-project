from typing import Dict, Any, List
from rag.base import BaseQueryEngine
from common.config import Config
from common.gemini_client import GeminiClient
from common.vector_store import VectorStore

RAG_PROMPT_TEMPLATE = """Sei un assistente tecnico esperto per cataloghi industriali. 
Rispondi alla domanda dell'utente basandoti ESCLUSIVAMENTE sul contesto fornito, estratto dalle descrizioni delle pagine del catalogo.

CONTESTO RECUPERATO:
{context}

ISTRUZIONI:
- Rispondi in modo preciso, professionale e chiaro in lingua italiana.
- Cita esplicitamente le fonti (file e numero pagina) da cui traggo le informazioni principali.
- Se il contesto non contiene informazioni sufficienti per rispondere con certezza, dichiara chiaramente che l'informazione non è presente nei documenti forniti. Non inventare dati.

DOMANDA DELL'UTENTE:
{query}
"""


class StandardQueryEngine(BaseQueryEngine):
    """
    Motore RAG Standard:
    Esegue la ricerca semantica su ChromaDB sulle descrizioni delle pagine e genera la risposta.
    """

    def __init__(self, config: Config, gemini_client: GeminiClient, vector_store: VectorStore):
        self.config = config
        self.gemini_client = gemini_client
        self.vector_store = vector_store
        self.top_k = config.rag.similarity_top_k

    def answer_query(self, query: str, source_filter: str = None) -> Dict[str, Any]:
        if not query or not query.strip():
            raise ValueError("La domanda non può essere vuota.")

        where = {"source": source_filter} if source_filter else None

        print(f"🔍 Ricerca semantica su ChromaDB per: '{query}'" + 
            (f" [filtro: {source_filter}]" if source_filter else ""))

        try:
            search_results = self.vector_store.query(
                query_text=query,
                n_results=self.top_k,
                where=where
            )
        except Exception as e:
            return {
                "query": query,
                "answer": f"❌ Errore durante la ricerca nel database vettoriale: {str(e)}",
                "sources": []
            }

        documents = search_results.get("documents", [[]])[0]
        metadatas = search_results.get("metadatas", [[]])[0]

        if not documents:
            return {
                "query": query,
                "answer": "Non ho trovato alcuna informazione pertinente nel database.",
                "sources": []
            }

        formatted_blocks: List[str] = []
        sources: List[Dict[str, Any]] = []

        for idx, (doc_text, meta) in enumerate(zip(documents, metadatas)):
            source_file = meta.get("source", "Documento Sconosciuto")
            page_num = meta.get("page", "?")

            block = f"[FONTE {idx+1}: {source_file} - Pagina {page_num}]\n{doc_text}"
            formatted_blocks.append(block)

            sources.append({
                "source": source_file,
                "page": page_num,
                "snippet": doc_text[:150] + "..."
            })

        full_context = "\n\n---\n\n".join(formatted_blocks)

        prompt = RAG_PROMPT_TEMPLATE.format(
            context=full_context,
            query=query
        )

        print(f"🤖 Generazione risposta con {self.config.rag.llm_model}...")
        try:
            answer_text = self.gemini_client.generate_text(
                prompt=prompt,
                model_name=self.config.rag.llm_model,
                temperature=0.2
            )
        except Exception as e:
            answer_text = f"⚠️ Le fonti nel database sono state trovate, ma è stato impossibile generare la risposta riassuntiva a causa di un errore API (es. Quota superata o offline).\n\nDettaglio: {str(e)}"

        return {
            "query": query,
            "answer": answer_text.strip(),
            "sources": sources
        }