"""Scrittura su disco del CSV e del report anomalie.

I file finiscono in ``data/exports/<specifica>/``:

- ``<documento>.csv``           l'indice del documento
- ``<documento>_anomalie.csv``  cosa controllare a mano (se ci sono anomalie)
- ``TUTTI.csv``                 l'unione di tutti i documenti (comando export --all)

La codifica di default è ``utf-8-sig``: UTF-8 con un marcatore iniziale che
permette a Excel di riconoscere correttamente le lettere accentate.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ragpdf.core.fileio import atomic_write_text
from ragpdf.indexing.assemble import AssembledIndex
from ragpdf.indexing.spec import IndexingSpec

ANOMALY_COLUMNS = ["livello", "pagina", "riga_csv", "colonna", "valore", "problema"]


def _to_csv(columns: list[str], rows: list[dict[str, str]], delimiter: str) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter=delimiter, lineterminator="\r\n",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write(path: Path, text: str, encoding: str) -> None:
    # atomic_write_text scrive in UTF-8: per altre codifiche si convertono prima
    # i caratteri non rappresentabili in "?" invece di fallire a metà file.
    if encoding.lower().replace("_", "-") not in ("utf-8", "utf-8-sig", "utf8"):
        text = text.encode(encoding, errors="replace").decode(encoding)
    atomic_write_text(path, text, encoding=encoding)


def write_index(index: AssembledIndex, spec: IndexingSpec, csv_path: Path) -> Path | None:
    """Scrive il CSV e, se servono, le anomalie. Restituisce il percorso delle anomalie."""
    _write(csv_path, _to_csv(index.columns, index.rows, spec.output.delimiter), spec.output.encoding)

    anomalies_path = csv_path.with_name(f"{csv_path.stem}_anomalie.csv")
    if not index.anomalies:
        anomalies_path.unlink(missing_ok=True)  # niente anomalie: niente file vecchi fuorvianti
        return None

    rows = [
        {
            "livello": a.level,
            "pagina": a.page or "",
            "riga_csv": a.csv_row or "",
            "colonna": a.column or "",
            "valore": a.value,
            "problema": a.problem,
        }
        for a in sorted(index.anomalies, key=lambda a: (a.page or 0, a.csv_row or 0))
    ]
    _write(anomalies_path, _to_csv(ANOMALY_COLUMNS, rows, spec.output.delimiter), spec.output.encoding)
    return anomalies_path
