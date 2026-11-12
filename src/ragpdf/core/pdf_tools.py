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
- frammenti duplicati sovrapposti (altro modo in cui compare il finto grassetto: la
  stessa parola ridisegnata con piccoli spostamenti), eliminati prima di ricomporre le
  righe;
- spazi e righe vuote superflue.

Il testo è un *aiuto*: perde la struttura delle tabelle e può contenere errori. La fonte
principale resta l'immagine della pagina.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pymupdf

# PyMuPDF non supporta l'uso contemporaneo da più thread: tutte le operazioni sui PDF
# passano da questo lucchetto (serve con l'interfaccia web, dove miniature e lavori in
# background possono arrivare insieme).
PDF_LOCK = threading.RLock()

_CID = re.compile(r"\(cid:\d+\)")
_SPACES = re.compile(r"[ \t]+")


def build_subset_pdf(source: Path, pages: list[int], target: Path) -> Path:
    """Crea ``target`` con le sole ``pages`` (numerate da 1) di ``source``, nell'ordine dato."""
    if not pages:
        raise ValueError("Nessuna pagina selezionata per l'estratto.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with PDF_LOCK, pymupdf.open(source) as src:
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


def _page_spans(page: pymupdf.Page) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Frammenti di testo della pagina, senza i duplicati sovrapposti del *finto grassetto*.

    Alcuni PDF simulano il grassetto disegnando lo stesso testo più volte spostato di
    frazioni di punto. Due frammenti con lo stesso testo e quasi la stessa posizione sono
    lo stesso testo: se ne tiene uno solo. Vale sia per parole intere sia per singole lettere.
    """
    spans: list[tuple[str, tuple[float, float, float, float]]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                tolerance = max(1.0, 0.25 * (y1 - y0))
                duplicate = any(
                    t == text and abs(b[0] - x0) <= tolerance and abs(b[1] - y0) <= tolerance
                    for t, b in spans
                )
                if not duplicate:
                    spans.append((text, (x0, y0, x1, y1)))
    return spans


def _spans_to_text(spans: list[tuple[str, tuple[float, float, float, float]]]) -> str:
    """Ricompone i frammenti in righe, dall'alto in basso e da sinistra a destra."""
    rows: list[list[tuple[str, tuple[float, float, float, float]]]] = []
    for text, box in sorted(spans, key=lambda s: ((s[1][1] + s[1][3]) / 2, s[1][0])):
        center = (box[1] + box[3]) / 2
        height = box[3] - box[1]
        for row in rows:
            r_box = row[0][1]
            if abs((r_box[1] + r_box[3]) / 2 - center) <= 0.5 * max(height, r_box[3] - r_box[1]):
                row.append((text, box))
                break
        else:
            rows.append([(text, box)])
    lines = []
    for row in rows:
        row.sort(key=lambda s: s[1][0])
        parts = [row[0][0]]
        for (_, prev), (text, box) in zip(row, row[1:], strict=False):
            gap = box[0] - prev[2]
            parts.append((" | " if gap > 6 else " " if gap > 0.5 else "") + text)
        lines.append("".join(parts))
    return "\n".join(lines)


def page_text(pdf_path: Path, page_number: int, max_chars: int = 12000) -> str:
    """Testo nativo ripulito di una pagina (stringa vuota se la pagina è solo immagine)."""
    with PDF_LOCK, pymupdf.open(pdf_path) as doc:
        raw = _spans_to_text(_page_spans(doc[page_number - 1]))
    text = clean_text(raw)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... testo troncato ...]"
    return text


def has_text_layer(pdf_path: Path, sample: int = 5) -> bool:
    """True se il PDF contiene testo estraibile (controllando alcune pagine)."""
    with PDF_LOCK, pymupdf.open(pdf_path) as doc:
        n = len(doc)
        step = max(1, n // sample)
        return any(doc[i].get_text("text").strip() for i in range(0, n, step))


def render_thumbnail(pdf_path: Path, page_number: int, target: Path, dpi: int = 40) -> Path:
    """Miniatura di una pagina (con cache su disco)."""
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with PDF_LOCK, pymupdf.open(pdf_path) as doc:
            if not 1 <= page_number <= len(doc):
                raise ValueError(f"La pagina {page_number} non esiste.")
            doc[page_number - 1].get_pixmap(dpi=dpi).save(target)
    return target


def build_multi_subset_pdf(entries: list[tuple[Path, int]], target: Path) -> Path:
    """Estratto con pagine prese da più PDF, nell'ordine dato: ``[(pdf, pagina), ...]``."""
    if not entries:
        raise ValueError("Nessuna pagina selezionata per l'estratto.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with PDF_LOCK:
        out = pymupdf.open()
        try:
            opened: dict[Path, pymupdf.Document] = {}
            try:
                for path, page in entries:
                    src = opened.setdefault(path, pymupdf.open(path))
                    if not 1 <= page <= len(src):
                        raise ValueError(f"La pagina {page} non esiste in {path.name} ({len(src)} pagine).")
                    out.insert_pdf(src, from_page=page - 1, to_page=page - 1)
            finally:
                for src in opened.values():
                    src.close()
            out.save(target, garbage=3, deflate=True)
        finally:
            out.close()
    return target


def labeled_mapping_text(entries: list[tuple[str, int]], documents_count: int) -> str:
    """Corrispondenza delle pagine di un estratto da più documenti: ``[(etichetta, pagina), ...]``."""
    lines = [f"- pagina {i} dell'estratto = {label}, pagina {p}" for i, (label, p) in enumerate(entries, 1)]
    return (
        f"Il PDF allegato è un estratto di {len(entries)} pagine scelte da {documents_count} documenti "
        f"con la stessa struttura. Corrispondenza delle pagine:\n" + "\n".join(lines)
    )
