import yaml
from pathlib import Path
from pydantic import BaseModel, Field

from common.paths import DEFAULT_CONFIG_FILE, PROJECT_ROOT

class AppSettings(BaseModel):
    name: str = "RAG Application"
    environment: str = "development"

class IngestionSettings(BaseModel):
    mode: str = Field(default="standard", description="Modalità di ingestion: standard o catalog")
    chunk_size: int = 500
    chunk_overlap: int = 50
    primer_path: str = "configs/primers/default_primer.md"
    render_dpi: int = 150

class RAGSettings(BaseModel):
    mode: str = Field(default="standard", description="Modalità di query: standard o hybrid")
    llm_model: str = "gemini-2.5-flash"
    flash_model: str = "gemini-2.5-flash"
    vector_db_path: str = "rag/store"
    collection_name: str = "catalog_documents"
    similarity_top_k: int = 4

class Config(BaseModel):
    app: AppSettings
    ingestion: IngestionSettings
    rag: RAGSettings

def load_config(config_path: str = None) -> Config:
    """
    Carica il file YAML risolvendo qualsiasi percorso relativo rispetto a PROJECT_ROOT.
    """
    if config_path is None:
        target_file = DEFAULT_CONFIG_FILE
    else:
        target_file = Path(config_path)
        if not target_file.is_absolute():
            target_file = PROJECT_ROOT / target_file

    if not target_file.exists():
        raise FileNotFoundError(f"File di configurazione non trovato in: {target_file}")
        
    with open(target_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    return Config(**data)