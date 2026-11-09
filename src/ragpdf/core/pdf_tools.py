"""Operazioni sui PDF: estratti di pagine e testo nativo.

**Estratto (PDF ridotto).** Per far capire a Gemini com'è fatto un catalogo non serve
inviarlo tutto: bastano le pagine significative (indice, legende, alcune schede tipo).
:func:`build_subset_pdf` crea un PDF con solo quelle pagine. Le pagine *non* vengono
modificate: la corrispondenza tra pagine dell'estratto e pagine originali viene spiegata
a Gemini nel prompt (:func:`page_mapping_text`).

Vantaggi rispetto a inviare immagini: un solo allegato leggero, ordine delle pagine
preservato e, soprattutto, il **testo nativo** del PDF resta disponibile a Gemini, che
così legge codici e sigle in modo esatto.

**Testo nativo di una pagina.** :func:`page_text` estrae il testo con PyMuPDF e lo
ripulisce dagli artefatti più comuni:

- ``(cid:12)``: glifi senza corrispondenza Unicode;
- *finto grassetto*: alcuni PDF simulano il grassetto stampando ogni lettera tre volte
  sovrapposta, e l'estrazione restituisce ``LLLOOOMMMBBBAAARRRDDDIIINNNIII``. Si corregge
  solo se **ogni** carattere della parola è ripetuto esattamente tre volte e la parola
  contiene lettere: codici come ``1100``, ``FIN040RS00`` o ``III`` restano intatti;
- spazi e righe vuote superflue.

Il testo è un *aiuto*: perde la struttura delle tabelle e può contenere errori. La fonte
principale resta l'immagine della pagina.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

_CID = re.compile(r"\(cid:\d+\)")
_SPACES = re.compile(r"[ \t]+")


def build_subset_pdf(source: Path, pages: list[int], target: Path) -> Path:
    """Crea ``target`` con le sole ``pages`` (numerate da 1) di ``source``, nell'ordine dato."""
    if not pages:
        raise ValueError("Nessuna pagina selezionata per l'estratto.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(source) as src:
        out = pymupdf.open()
        try:
            for p in pages:
                if not 1 <= p <= len(src):
                    raise ValueError(f"La pagina {p} non esiste (il documento ha {len(src)} pagine).")
                out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
            out.save(target, garbage=3, deflate=True)
        finally:
            out.close()
    return target


def page_mapping_text(pages: list[int], total_pages: int) -> str:
    """Descrizione, per il prompt, di quali pagine originali contiene l'estratto."""
    lines = [f"- pagina {i} dell'estratto = pagina {p} del documento originale" for i, p in enumerate(pages, 1)]
    return (
        f"Il PDF allegato NON è il documento completo: è un estratto di {len(pages)} pagine "
        f"scelte da un documento di {total_pages} pagine. Corrispondenza delle pagine:\n" + "\n".join(lines)
    )


def _fix_fake_bold(word: str) -> str:
    """``LLLOOOMMM`` -> ``LOM``; i codici come ``1100`` o ``FIN040RS00`` restano intatti."""
    n = 3
    if len(word) < 2 * n or len(word) % n or not any(c.isalpha() for c in word):
        return word
    chunks = [word[i : i + n] for i in range(0, len(word), n)]
    if all(len(set(c)) == 1 for c in chunks):
        return "".join(c[0] for c in chunks)
    return word


def clean_text(text: str) -> str:
    text = _CID.sub(" ", text)
    lines = []
    for raw in text.splitlines():
        words = [_fix_fake_bold(w) for w in _SPACES.split(raw.strip()) if w]
        if words:
            lines.append(" ".join(words))
    return "\n".join(lines)


def page_text(pdf_path: Path, page_number: int, max_chars: int = 12000) -> str:
    """Testo nativo ripulito di una pagina (stringa vuota se la pagina è solo immagine)."""
    with pymupdf.open(pdf_path) as doc:
        raw = doc[page_number - 1].get_text("text", sort=True)
    text = clean_text(raw)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... testo troncato ...]"
    return text


def has_text_layer(pdf_path: Path, sample: int = 5) -> bool:
    """True se il PDF contiene testo estraibile (controllando alcune pagine)."""
    with pymupdf.open(pdf_path) as doc:
        n = len(doc)
        step = max(1, n // sample)
        return any(doc[i].get_text("text").strip() for i in range(0, n, step))
