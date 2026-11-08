"""Caricamento dei prompt da ``configs/prompts/``.

I prompt sono file Markdown modificabili senza toccare il codice. I segnaposto
si scrivono ``{{nome}}``. Non si usa ``str.format`` perché primer e istruzioni
dell'utente possono contenere parentesi graffe (es. esempi JSON), che
``format`` interpreterebbe come segnaposto.
"""

from __future__ import annotations

import re

from ragpdf.core.paths import PROMPTS_DIR

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{name}' non trovato in {PROMPTS_DIR}.")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, /, **values: object) -> str:
    """Sostituisce i segnaposto ``{{nome}}``; un segnaposto senza valore è un errore."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Manca il valore per il segnaposto '{{{{{key}}}}}' nel prompt.")
        return str(values[key])

    return _PLACEHOLDER.sub(replace, template)
