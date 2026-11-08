"""Percorsi fissi dell'applicazione.

Qui stanno solo i percorsi dell'*applicazione* (configurazione, prompt, esempi).
I percorsi dei *dati dell'utente* (PDF, primer, risultati) sono gestiti da
:class:`ragpdf.core.workspace.Workspace`, perché la cartella del workspace è
configurabile.

La radice del progetto si trova così, in ordine:

1. variabile d'ambiente ``RAGPDF_HOME``, se impostata;
2. la cartella che contiene ``pyproject.toml`` e ``configs/`` risalendo da questo
   file (caso tipico: repository clonato e installato con ``pip install -e .``);
3. la cartella da cui si lancia il comando.
"""

from __future__ import annotations

import os
from pathlib import Path


def _find_project_root() -> Path:
    env_home = os.getenv("RAGPDF_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "configs").is_dir():
            return parent

    return Path.cwd().resolve()


PROJECT_ROOT = _find_project_root()

ENV_FILE = PROJECT_ROOT / ".env"
CONFIGS_DIR = PROJECT_ROOT / "configs"
DEFAULT_CONFIG_FILE = CONFIGS_DIR / "default.yaml"
PROMPTS_DIR = CONFIGS_DIR / "prompts"
EXAMPLES_DIR = PROJECT_ROOT / "examples"


def resolve_path(path: str | Path) -> Path:
    """Rende assoluto un percorso relativo, interpretandolo rispetto alla radice del progetto."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p
