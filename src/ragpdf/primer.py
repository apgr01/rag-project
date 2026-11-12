"""Generazione automatica della bozza di un primer.

Il primer spiega all'AI com'è fatto un documento nel suo complesso. Qui si chiede a
Gemini di scriverne una bozza, che l'utente poi rivede.

Cosa riceve Gemini:

- **pagine scelte dall'utente** (consigliato per i cataloghi): un *estratto* PDF con le
  sole pagine indicate (indice, legende, alcune schede tipo). Nel prompt si spiega che è
  un estratto e si riporta la corrispondenza con le pagine originali;
- **nessuna scelta**: il PDF intero se è abbastanza piccolo, altrimenti un estratto
  automatico (le prime pagine, dove di solito stanno indice e legende, più alcune
  distribuite nel resto del documento).

La sessione guidata (``ragpdf setup``) usa le stesse funzioni per scegliere le pagine.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ragpdf.core.documents import Document
from ragpdf.core.gemini import LLMClient
from ragpdf.core.pdf_tools import build_subset_pdf, page_mapping_text
from ragpdf.core.prompts import load_prompt, render_prompt

WHOLE_PDF_MAX_BYTES = 40 * 1024 * 1024
WHOLE_PDF_MAX_PAGES = 60          # oltre, meglio un estratto: più veloce e mirato
SAMPLE_FIRST_PAGES = 8
SAMPLE_TOTAL_PAGES = 20
MAX_SELECTED_PAGES = 40           # pagine massime in un estratto scelto dall'utente


def sample_pages(page_count: int, first: int = SAMPLE_FIRST_PAGES, total: int = SAMPLE_TOTAL_PAGES) -> list[int]:
    """Le prime ``first`` pagine più altre distribuite uniformemente fino a ``total``."""
    if page_count <= total:
        return list(range(1, page_count + 1))
    chosen = set(range(1, first + 1))
    remaining = total - len(chosen)
    step = (page_count - first) / remaining
    chosen.update(first + 1 + int(i * step) for i in range(remaining))
    return sorted(p for p in chosen if p <= page_count)


def clean_markdown(text: str) -> str:
    """Toglie l'eventuale blocco ```markdown ... ``` che a volte avvolge la risposta."""
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, flags=re.DOTALL)
    return (match.group(1) if match else text).strip() + "\n"


def choose_pages(doc: Document, pages: list[int] | None) -> tuple[list[int] | None, str]:
    """Decide cosa inviare: ``(None, ...)`` = PDF intero, altrimenti le pagine dell'estratto."""
    if pages:
        if len(pages) > MAX_SELECTED_PAGES:
            raise ValueError(
                f"Hai selezionato {len(pages)} pagine: il massimo è {MAX_SELECTED_PAGES}. "
                "Scegli solo quelle più rappresentative (indice, legende, alcune pagine tipo)."
            )
        return pages, f"estratto di {len(pages)} pagine scelte"
    small = doc.pdf_path.stat().st_size <= WHOLE_PDF_MAX_BYTES
    if small and doc.info.page_count <= WHOLE_PDF_MAX_PAGES:
        return None, "PDF intero"
    auto = sample_pages(doc.info.page_count)
    return auto, f"estratto automatico di {len(auto)} pagine"


def generate_primer(doc: Document, client: LLMClient, pages: list[int] | None = None) -> tuple[str, str]:
    """Restituisce ``(testo del primer, descrizione di cosa è stato inviato)``."""
    chosen, description = choose_pages(doc, pages)
    if chosen is None:
        note = f"Il PDF allegato è il documento completo ({doc.info.page_count} pagine)."
        prompt = render_prompt(load_prompt("primer_generate"), pages_note=note)
        return clean_markdown(client.generate_text(prompt, pdf=doc.pdf_path)), description

    note = page_mapping_text(chosen, doc.info.page_count) + (
        "\nNella sezione sulla struttura usa i numeri di pagina del documento ORIGINALE. Le parti "
        "del documento che non vedi deducile dall'indice, se presente; altrimenti indica che non sono note."
    )
    prompt = render_prompt(load_prompt("primer_generate"), pages_note=note)
    with tempfile.TemporaryDirectory() as tmp:
        subset = build_subset_pdf(doc.pdf_path, chosen, Path(tmp) / "estratto.pdf")
        return clean_markdown(client.generate_text(prompt, pdf=subset)), description
