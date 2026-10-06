import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings

from common.paths import VECTOR_STORE_DIR, PROJECT_ROOT

class VectorStore:
    def __init__(self, db_path: Optional[str] = None, collection_name: str = "catalog_documents"):
        if db_path is None:
            self.db_path = VECTOR_STORE_DIR
        else:
            p = Path(db_path)
            self.db_path = p if p.is_absolute() else PROJECT_ROOT / p
            
        self.collection_name = collection_name
        os.makedirs(self.db_path, exist_ok=True)
        
        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(allow_reset=True, anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(name=self.collection_name)

    def add_documents(self, ids: List[str], documents: List[str], metadatas: Optional[List[Dict[str, Any]]] = None):
        if not ids or not documents:
            return
        self.collection.add(ids=ids, documents=documents, metadatas=metadatas)

    def query(self, query_text: str, n_results: int = 4, where: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where
        )
        return results

    def reset_collection(self):
        self.client.delete_collection(name=self.collection_name)
        self.collection = self.client.get_or_create_collection(name=self.collection_name)

    def count(self) -> int:
        return self.collection.count()