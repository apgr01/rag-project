"""Rendering: trasforma le pagine di un PDF in immagini PNG.

È il primo passo di ogni elaborazione "vision-first". Le immagini vengono
salvate nella cartella ``pages/`` del documento e riutilizzate: si rigenerano
solo se mancano o se è cambiata la risoluzione (DPI) in configurazione.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from tqdm import tqdm

from ragpdf.core.fileio import read_json, write_json

_INFO_FILE = "render.json"


def page_image_path(pages_dir: Path, page_number: int) -> Path:
    return pages_dir / f"page-{page_number:04d}.png"


def render_pages(
    pdf_path: Path,
    pages_dir: Path,
    dpi: int,
    pages: list[int] | None = None,
    force: bool = False,
    show_progress: bool = True,
) -> dict[int, Path]:
    """Garantisce che esistano le immagini delle pagine richieste.

    Args:
        pages: numeri di pagina (da 1). ``None`` = tutte.
        force: rigenera anche le immagini già presenti.

    Returns:
        Dizionario ``numero pagina -> percorso immagine``.
    """
    pages_dir.mkdir(parents=True, exist_ok=True)
    info_path = pages_dir / _INFO_FILE
    previous_dpi = read_json(info_path).get("dpi") if info_path.exists() else None
    if previous_dpi is not None and previous_dpi != dpi:
        force = True  # risoluzione cambiata: le vecchie immagini non vanno più bene

    with pymupdf.open(pdf_path) as pdf:
        wanted = pages or list(range(1, len(pdf) + 1))
        result = {n: page_image_path(pages_dir, n) for n in wanted}
        missing = [n for n, p in result.items() if force or not p.exists()]

        iterator = tqdm(missing, desc="Rendering pagine", unit="pag", disable=not show_progress or not missing)
        for n in iterator:
            pix = pdf[n - 1].get_pixmap(dpi=dpi)
            pix.save(result[n])

    write_json(info_path, {"dpi": dpi})
    return result


def parse_page_ranges(text: str | None, total_pages: int) -> list[int] | None:
    """Interpreta selezioni di pagine come ``"1-5, 8, 20-"``.

    Restituisce ``None`` se il testo è vuoto (= tutte le pagine).
    Gli intervalli aperti (``20-``) arrivano fino all'ultima pagina.
    """
    if text is None or not str(text).strip():
        return None

    selected: set[int] = set()
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start_s, end_s = part.split("-", 1)
                start = int(start_s) if start_s.strip() else 1
                end = int(end_s) if end_s.strip() else total_pages
            else:
                start = end = int(part)
        except ValueError as e:
            raise ValueError(f"Selezione di pagine non valida: '{part}' (esempio: 1-5,8,20-)") from e
        if start < 1 or end > total_pages or start > end:
            raise ValueError(f"Intervallo '{part}' fuori dal documento (pagine 1-{total_pages}).")
        selected.update(range(start, end + 1))
    return sorted(selected)
