from pathlib import Path

# Determina in modo assoluto la radice del progetto
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# File e cartelle di configurazione
ENV_FILE = PROJECT_ROOT / ".env"
CONFIGS_DIR = PROJECT_ROOT / "configs"
DEFAULT_CONFIG_FILE = CONFIGS_DIR / "default.yaml"
PRIMERS_DIR = CONFIGS_DIR / "primers"

# Cartelle dati
DATA_DIR = PROJECT_ROOT / "data"
PDFS_DIR = DATA_DIR / "pdfs"
IMAGES_DIR = DATA_DIR / "images"
DESCRIPTIONS_DIR = DATA_DIR / "descriptions"

# Database Vettoriale
VECTOR_STORE_DIR = PROJECT_ROOT / "rag" / "store"