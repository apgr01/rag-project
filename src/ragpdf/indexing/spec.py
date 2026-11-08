"""Specifica di indicizzazione: *cosa* estrarre da un documento e *come* scriverlo.

È un file YAML scritto dall'utente (in ``data/specs/<nome>.yaml``). Il programma
non sa nulla di cataloghi, motori o codici: tutto ciò che riguarda il contenuto
del CSV sta nella specifica. Esempio minimo::

    name: ricambi
    row: Una riga per ogni codice articolo presente nella pagina.
    columns:
      - name: CODICE
        description: Codice articolo, esattamente come stampato.
        required: true
      - name: DESCRIZIONE
        description: Descrizione breve del pezzo, in italiano.

Campi principali:

- ``row``: cosa rappresenta una riga del CSV (la decisione più importante);
- ``columns``: le colonne, nell'ordine del CSV. Per ognuna una descrizione in
  linguaggio naturale; opzionali: ``type`` (text / integer / number),
  ``required``, ``context``, ``pattern`` (espressione regolare di controllo),
  ``allowed_values``, ``default``;
- ``instructions``: regole libere, scritte come le diresti a una persona
  ("se non trovi il codice OEM lascia vuoto", ...);
- ``mappings``: tabelle di conversione (es. sigla → nome componente);
- ``examples``: un CSV d'esempio da usare come riferimento di formato;
- ``pages``: pagine da elaborare (es. ``"20-"`` per saltare le introduttive);
- ``output``: formato del CSV (separatore, codifica, colonne aggiuntive).

Le colonne ``context: true`` descrivono informazioni valide per tutta la pagina
(es. il modello di motore nell'intestazione). Se su una pagina mancano, e
``inherit_context`` è attivo, vengono ereditate dalla pagina precedente: copre il
caso delle tabelle che continuano su più pagine senza ripetere l'intestazione.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ragpdf.core.workspace import slugify


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnSpec(_Strict):
    name: str
    description: str = ""
    type: Literal["text", "integer", "number"] = "text"
    required: bool = False
    context: bool = False
    pattern: str | None = None
    allowed_values: list[str] | None = None
    default: str | None = None

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"espressione regolare non valida: {e}") from e
        return v


class MappingSpec(_Strict):
    description: str = ""
    values: dict[str, str]

    @field_validator("values", mode="before")
    @classmethod
    def _stringify(cls, v: dict) -> dict:
        # In YAML "01: X" diventa una chiave numerica: la riportiamo a testo.
        return {str(k): str(val) for k, val in (v or {}).items()}


class ExamplesSpec(_Strict):
    csv: str | None = Field(None, description="Percorso di un CSV d'esempio (relativo alla specifica).")
    max_rows: int = Field(20, ge=1, le=200)


class OutputSpec(_Strict):
    delimiter: str = ","
    encoding: str = Field("utf-8-sig", description="utf-8-sig = UTF-8 leggibile da Excel")
    page_column: str | None = "PAGINA_PDF"
    source_column: str | None = "FILE_ORIGINE"
    deduplicate: bool = True
    sort_by: list[str] = []

    @field_validator("delimiter")
    @classmethod
    def _one_char(cls, v: str) -> str:
        if v == "\\t":
            return "\t"
        if len(v) != 1:
            raise ValueError("il separatore deve essere un solo carattere (es. ',' o ';')")
        return v


class IndexingSpec(_Strict):
    name: str
    description: str = ""
    row: str = Field(..., description="Cosa rappresenta una riga del CSV.")
    instructions: str = ""
    columns: list[ColumnSpec] = Field(..., min_length=1)
    mappings: dict[str, MappingSpec] = {}
    examples: ExamplesSpec = ExamplesSpec()
    pages: str | None = None
    inherit_context: bool = True
    output: OutputSpec = OutputSpec()

    # Cartella del file YAML: serve a risolvere i percorsi relativi (es. examples.csv).
    base_dir: Path | None = Field(None, exclude=True)

    @model_validator(mode="after")
    def _check_columns(self) -> IndexingSpec:
        names = [c.name for c in self.columns]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"colonne duplicate: {', '.join(sorted(dupes))}")
        extra = {self.output.page_column, self.output.source_column} - {None}
        clash = extra & set(names)
        if clash:
            raise ValueError(f"le colonne {clash} coincidono con quelle aggiunte dall'output")
        unknown = set(self.output.sort_by) - set(self.output_columns)
        if unknown:
            raise ValueError(f"output.sort_by contiene colonne inesistenti: {', '.join(sorted(unknown))}")
        return self

    # --- proprietà derivate --------------------------------------------------------
    @property
    def context_columns(self) -> list[ColumnSpec]:
        return [c for c in self.columns if c.context]

    @property
    def output_columns(self) -> list[str]:
        cols = [c.name for c in self.columns]
        if self.output.page_column:
            cols.append(self.output.page_column)
        if self.output.source_column:
            cols.append(self.output.source_column)
        return cols

    def json_keys(self) -> dict[str, str]:
        """Nome colonna → chiave JSON sicura (``"NR. CILINDRI"`` → ``"nr_cilindri"``)."""
        keys: dict[str, str] = {}
        used: set[str] = set()
        for col in self.columns:
            base = slugify(col.name).replace("-", "_") or "colonna"
            if base[0].isdigit():
                base = f"c_{base}"
            key, n = base, 2
            while key in used:
                key, n = f"{base}_{n}", n + 1
            used.add(key)
            keys[col.name] = key
        return keys

    # --- esempi --------------------------------------------------------------------
    def example_csv_path(self) -> Path | None:
        if not self.examples.csv:
            return None
        p = Path(self.examples.csv).expanduser()
        if not p.is_absolute() and self.base_dir is not None:
            p = self.base_dir / p
        return p

    def example_text(self) -> str:
        """Le prime righe del CSV d'esempio, come testo; stringa vuota se non c'è."""
        path = self.example_csv_path()
        if path is None:
            return ""
        if not path.exists():
            raise FileNotFoundError(f"CSV d'esempio non trovato: {path}")
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = raw.splitlines()[: self.examples.max_rows + 1]
        return "\n".join(lines)

    # --- impronta ------------------------------------------------------------------
    def extraction_fingerprint(self, primer_text: str, model: str) -> str:
        """Impronta di tutto ciò che influenza le risposte di Gemini.

        Se cambia (nuove colonne, istruzioni diverse, primer modificato, altro
        modello) i risultati già estratti non sono più coerenti con la specifica
        e il programma propone di rifare l'estrazione. Le modifiche alla sola
        sezione ``output`` o ai controlli (``required``) invece non la
        richiedono: basta rigenerare il CSV.
        """
        relevant = {
            "row": self.row,
            "instructions": self.instructions,
            "columns": [c.model_dump(exclude={"required"}) for c in self.columns],
            "mappings": {k: v.model_dump() for k, v in self.mappings.items()},
            "examples": self.example_text(),
            "primer": primer_text,
            "model": model,
        }
        blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class SpecError(Exception):
    """Specifica mancante o non valida, con messaggio leggibile."""


def load_spec(path: Path) -> IndexingSpec:
    if not path.exists():
        raise SpecError(f"Specifica non trovata: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SpecError(f"{path.name} non è YAML valido:\n{e}") from e
    if not isinstance(data, dict):
        raise SpecError(f"{path.name} deve contenere un elenco di campi (name, row, columns, ...).")
    data.setdefault("name", path.stem)
    try:
        spec = IndexingSpec.model_validate(data)
    except ValidationError as e:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc']) or '(radice)'}: {err['msg']}"
            for err in e.errors()
        )
        raise SpecError(f"Problemi nella specifica {path.name}:\n{problems}") from e
    spec.base_dir = path.parent
    return spec


def read_csv_rows(path: Path, delimiter: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    """Legge un CSV (indovinando il separatore se non indicato). Usato per confronti e test."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return list(reader.fieldnames or []), list(reader)
