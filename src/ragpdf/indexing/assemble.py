"""Assemblaggio: dai risultati per pagina al CSV finale.

Questa fase **non chiama Gemini**: lavora solo sui risultati già salvati, quindi
è veloce, ripetibile e prevedibile. Passi, nell'ordine:

1. **Contesto di pagina** — le colonne ``context`` vuote in una riga prendono il
   valore dell'intestazione della pagina; se anche questa manca (e
   ``inherit_context`` è attivo) quello dell'ultima pagina che lo aveva.
2. **Normalizzazione** — spazi superflui rimossi; per le colonne ``integer``
   ``"4.0"`` diventa ``"4"``; valori di ``default`` dove la cella è vuota.
3. **Colonne aggiuntive** — pagina del PDF e nome del file d'origine.
4. **Duplicati** — righe identiche rimosse (se ``output.deduplicate``).
5. **Ordinamento** — per ``output.sort_by``, altrimenti per pagina.
6. **Controlli** — campi obbligatori, formati, valori ammessi, pagine non
   elaborate e note del modello finiscono nel **report anomalie**, da
   verificare a mano. Le righe con anomalie restano nel CSV: decide l'utente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ragpdf.indexing.spec import ColumnSpec, IndexingSpec

ERROR, WARNING, INFO = "errore", "avviso", "info"

_SPACES = re.compile(r"\s+")
_INTEGER_LIKE = re.compile(r"^[+-]?\d+(?:[.,]0+)?$")
_NUMBER_LIKE = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:[.,\s]\d{3})+)(?:[.,]\d+)?$")


@dataclass
class Anomaly:
    level: str
    problem: str
    page: int | None = None
    csv_row: int | None = None
    column: str | None = None
    value: str = ""


@dataclass
class AssembledIndex:
    columns: list[str]
    rows: list[dict[str, str]]
    anomalies: list[Anomaly] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _normalize(col: ColumnSpec, value: str) -> str:
    value = _SPACES.sub(" ", value).strip()
    if not value and col.default is not None:
        return col.default
    if col.type == "integer" and _INTEGER_LIKE.match(value):
        return str(int(float(value.replace(",", "."))))
    return value


def _check(col: ColumnSpec, value: str) -> str | None:
    """Restituisce la descrizione del problema, o None se il valore va bene."""
    if not value:
        return "campo obbligatorio vuoto" if col.required else None
    if col.type == "integer" and not re.fullmatch(r"[+-]?\d+", value):
        return "non è un numero intero"
    if col.type == "number" and not _NUMBER_LIKE.match(value):
        return "non è un numero"
    if col.pattern and not re.fullmatch(col.pattern, value):
        return f"non rispetta il formato {col.pattern}"
    if col.allowed_values and value not in col.allowed_values:
        return "valore non tra quelli ammessi"
    return None


def assemble(
    spec: IndexingSpec,
    page_results: dict[int, dict[str, Any]],
    source_filename: str,
    expected_pages: list[int],
    failed_pages: dict[int, str] | None = None,
) -> AssembledIndex:
    failed_pages = failed_pages or {}
    columns = spec.output_columns
    by_name = {c.name: c for c in spec.columns}
    context_names = [c.name for c in spec.context_columns]
    anomalies: list[Anomaly] = []
    stats = {"righe_estratte": 0, "valori_ereditati": 0, "duplicati_rimossi": 0}

    # --- pagine non elaborate ------------------------------------------------------
    missing = [p for p in expected_pages if p not in page_results]
    for p in missing:
        reason = failed_pages.get(p)
        anomalies.append(
            Anomaly(ERROR, f"pagina non elaborata ({reason})" if reason else "pagina non ancora elaborata", page=p)
        )

    # --- 1-3: righe con contesto, normalizzate, con colonne aggiuntive -------------
    rows: list[tuple[int, dict[str, str]]] = []
    last_context: dict[str, str] = {}
    for page in sorted(page_results):
        result = page_results[page]
        own = {k: v for k, v in (result.get("page_context") or {}).items() if v}
        context = dict(own)
        inherited: set[str] = set()
        if spec.inherit_context:
            for name in context_names:
                if name not in context and name in last_context:
                    context[name] = last_context[name]
                    inherited.add(name)
        last_context.update(own)

        if result.get("notes"):
            anomalies.append(Anomaly(INFO, f"nota del modello: {result['notes']}", page=page))

        for raw in result.get("rows", []):
            row: dict[str, str] = {}
            for name, col in by_name.items():
                value = str(raw.get(name, "") or "")
                if not value.strip() and name in context:
                    value = context[name]
                    if name in inherited:
                        stats["valori_ereditati"] += 1
                row[name] = _normalize(col, value)
            if not any(row.values()):
                continue
            if spec.output.page_column:
                row[spec.output.page_column] = str(page)
            if spec.output.source_column:
                row[spec.output.source_column] = source_filename
            rows.append((page, row))

    stats["righe_estratte"] = len(rows)

    # --- 4: duplicati ----------------------------------------------------------------
    if spec.output.deduplicate:
        seen: set[tuple[str, ...]] = set()
        unique = []
        for page, row in rows:
            key = tuple(row.get(c, "") for c in columns)
            if key in seen:
                stats["duplicati_rimossi"] += 1
                continue
            seen.add(key)
            unique.append((page, row))
        rows = unique

    # --- 5: ordinamento --------------------------------------------------------------
    if spec.output.sort_by:
        rows.sort(key=lambda pr: tuple(pr[1].get(c, "") for c in spec.output.sort_by))
    # (altrimenti restano in ordine di pagina e di lettura, che è già stabile)

    # --- 6: controlli ----------------------------------------------------------------
    for i, (page, row) in enumerate(rows, start=2):  # riga 1 del CSV = intestazione
        for name, col in by_name.items():
            problem = _check(col, row[name])
            if problem:
                level = ERROR if problem == "campo obbligatorio vuoto" else WARNING
                anomalies.append(Anomaly(level, problem, page, i, name, row[name]))

    pages_with_rows = {p for p, _ in rows}
    stats.update(
        pagine_previste=len(expected_pages),
        pagine_elaborate=len([p for p in expected_pages if p in page_results]),
        pagine_con_righe=len(pages_with_rows),
        righe_finali=len(rows),
        errori=sum(a.level == ERROR for a in anomalies),
        avvisi=sum(a.level == WARNING for a in anomalies),
    )
    return AssembledIndex(columns, [r for _, r in rows], anomalies, stats)


def merge(spec: IndexingSpec, parts: list[AssembledIndex]) -> AssembledIndex:
    """Unisce gli indici di più documenti (stessa specifica) in uno solo."""
    columns = spec.output_columns
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    for part in parts:
        for row in part.rows:
            key = tuple(row.get(c, "") for c in columns)
            if spec.output.deduplicate and key in seen:
                duplicates += 1
                continue
            seen.add(key)
            rows.append(row)
    if spec.output.sort_by:
        rows.sort(key=lambda r: tuple(r.get(c, "") for c in spec.output.sort_by))
    stats = {"documenti": len(parts), "righe_finali": len(rows), "duplicati_rimossi": duplicates}
    return AssembledIndex(columns, rows, [], stats)
