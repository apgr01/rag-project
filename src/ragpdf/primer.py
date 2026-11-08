"""Generazione automatica della bozza di un primer.

Il primer spiega all'AI com'è fatto un documento nel suo complesso. Scriverlo a
mano richiede tempo; qui si chiede a Gemini di leggere **l'intero PDF** e di
produrne una bozza, che l'utente poi rivede.

Due strategie:

- **PDF intero** (preferita): il file viene caricato con la Files API di Gemini,
  che legge testo e immagini di tutte le pagine;
- **campione di pagine**: se il PDF è troppo grande per la Files API, si inviano
  le immagini a bassa risoluzione di un campione di pagine (le prime, dove di
  solito stanno indice e legende, più alcune distribuite nel resto).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ragpdf.core.documents import Document
from ragpdf.core.gemini import LLMClient
from ragpdf.core.prompts import load_prompt
from ragpdf.core.render import render_pages

MAX_PDF_BYTES = 45 * 1024 * 1024   # margine sotto il limite di 50 MB della Files API
MAX_PDF_PAGES = 900                # margine sotto il limite di 1000 pagine
SAMPLE_FIRST_PAGES = 15
SAMPLE_TOTAL_PAGES = 40
SAMPLE_DPI = 80


def sample_pages(page_count: int, first: int = SAMPLE_FIRST_PAGES, total: int = SAMPLE_TOTAL_PAGES) -> list[int]:
    """Le prime ``first`` pagine più altre distribuite uniformemente fino a ``total``."""
    if page_count <= total:
        return list(range(1, page_count + 1))
    chosen = set(range(1, first + 1))
    remaining = total - len(chosen)
    step = (page_count - first) / remaining
    chosen.update(first + 1 + int(i * step) for i in range(remaining))
    return sorted(p for p in chosen if p <= page_count)


def _clean_markdown(text: str) -> str:
    """Toglie l'eventuale blocco ```markdown ... ``` che a volte avvolge la risposta."""
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, flags=re.DOTALL)
    return (match.group(1) if match else text).strip() + "\n"


def generate_primer(doc: Document, client: LLMClient) -> tuple[str, str]:
    """Restituisce ``(testo del primer, strategia usata)``."""
    prompt = load_prompt("primer_generate")
    size = doc.pdf_path.stat().st_size

    if size <= MAX_PDF_BYTES and doc.info.page_count <= MAX_PDF_PAGES:
        return _clean_markdown(client.generate_text_from_pdf(prompt, doc.pdf_path)), "PDF intero"

    pages = sample_pages(doc.info.page_count)
    with tempfile.TemporaryDirectory() as tmp:
        images = render_pages(doc.pdf_path, Path(tmp), SAMPLE_DPI, pages, show_progress=False)
        note = (
            f"\n\nNOTA: il PDF è troppo grande per essere inviato intero. Ricevi le immagini "
            f"di {len(pages)} pagine su {doc.info.page_count}, nell'ordine: "
            f"{', '.join(map(str, pages))}. Tienine conto nella struttura macro."
        )
        text = client.generate_text(prompt + note, images=[images[p] for p in pages])
    return _clean_markdown(text), f"campione di {len(pages)} pagine"
