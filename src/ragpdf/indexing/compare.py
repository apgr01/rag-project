"""Confronto tra il CSV prodotto e un CSV di riferimento (es. un indice fatto a mano).

Serve a **misurare** la qualità dell'indicizzazione e a capire se una modifica a primer o
specifica la migliora. Le righe vengono confrontate su un insieme di *colonne chiave*
(di default: tutte le colonne in comune, escluse pagina e file d'origine), dopo una
normalizzazione leggera (spazi, maiuscole, "4.0" = "4").

Risultato:

- **trovate**: righe presenti in entrambi;
- **mancanti**: nel riferimento ma non nel CSV prodotto (il tool le ha perse o sbagliate);
- **in più**: nel CSV prodotto ma non nel riferimento (errori o righe non previste);
- **copertura** = trovate / righe del riferimento; **precisione** = trovate / righe prodotte.

Per confrontare solo una parte del documento (es. dopo una prova su poche pagine) si può
filtrare il riferimento per file d'origine e intervallo di pagine.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_SPACES = re.compile(r"\s+")
_INT_FLOAT = re.compile(r"^(\d+)[.,]0+$")


def normalize_value(value: str | None) -> str:
    v = _SPACES.sub(" ", (value or "").strip()).upper()
    m = _INT_FLOAT.match(v)
    return m.group(1) if m else v


@dataclass
class Comparison:
    key_columns: list[str]
    produced_rows: int
    reference_rows: int
    matched: int
    missing: list[dict[str, str]] = field(default_factory=list)
    extra: list[dict[str, str]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.matched / self.reference_rows if self.reference_rows else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.produced_rows if self.produced_rows else 1.0


def _key(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple(normalize_value(row.get(c)) for c in columns)


def compare_rows(
    produced: list[dict[str, str]],
    reference: list[dict[str, str]],
    key_columns: list[str],
) -> Comparison:
    """Confronto "a multinsieme": righe ripetute contano tante volte quante compaiono."""
    produced_keys = Counter(_key(r, key_columns) for r in produced)
    reference_keys = Counter(_key(r, key_columns) for r in reference)
    common = produced_keys & reference_keys

    def leftovers(rows: list[dict[str, str]], budget: Counter) -> list[dict[str, str]]:
        remaining = Counter(budget)
        out = []
        for r in rows:
            k = _key(r, key_columns)
            if remaining[k] > 0:
                remaining[k] -= 1
            else:
                out.append({c: r.get(c, "") for c in key_columns})
        return out

    return Comparison(
        key_columns=key_columns,
        produced_rows=sum(produced_keys.values()),
        reference_rows=sum(reference_keys.values()),
        matched=sum(common.values()),
        missing=leftovers(reference, common),
        extra=leftovers(produced, common),
    )


def filter_reference(
    rows: list[dict[str, str]],
    source_column: str | None,
    source_name: str | None,
    page_column: str | None,
    pages: list[int] | None,
) -> list[dict[str, str]]:
    """Tiene solo le righe del riferimento relative al documento e alle pagine indicate."""
    result = rows
    if source_column and source_name and rows and source_column in rows[0]:
        target = normalize_value(Path(source_name).stem)
        result = [r for r in result if normalize_value(Path(r.get(source_column, "")).stem) == target]
    if page_column and pages and rows and page_column in rows[0]:
        allowed = set(pages)

        def page_of(r: dict[str, str]) -> int | None:
            v = normalize_value(r.get(page_column))
            return int(v) if v.isdigit() else None

        result = [r for r in result if page_of(r) in allowed]
    return result
