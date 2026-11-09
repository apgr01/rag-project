"""Costruzione del prompt e dello schema JSON per l'estrazione di una pagina.

Il prompt è composto dal template ``configs/prompts/indexing_page.md`` più le
parti che dipendono dalla specifica (colonne, mappature, istruzioni, esempi).

Lo **schema JSON** viene passato a Gemini come *output strutturato*: il modello
è vincolato a rispondere con un oggetto di questa forma::

    {
      "page_summary": "...",
      "page_context": {"motore": "3LD 450/S", ...},   # solo colonne di contesto
      "rows": [{"costruttore": "...", "fin_code": "...", ...}, ...],
      "notes": "..."
    }

Tutti i valori sono stringhe: il CSV è testo, e la conversione/controllo dei
tipi avviene dopo, in modo deterministico (vedi assemble.py).
"""

from __future__ import annotations

from typing import Any

from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.indexing.spec import ColumnSpec, IndexingSpec

_TYPE_LABEL = {"text": "testo", "integer": "numero intero", "number": "numero"}


def _describe_column(col: ColumnSpec, key: str) -> str:
    parts = [f"- `{key}` → colonna CSV \"{col.name}\""]
    if col.description:
        parts.append(f": {col.description.strip()}")
    details = []
    if col.type != "text":
        details.append(f"tipo: {_TYPE_LABEL[col.type]}")
    if col.pattern:
        details.append(f"formato atteso (regex): {col.pattern}")
    if col.allowed_values:
        details.append("valori ammessi: " + ", ".join(f'"{v}"' for v in col.allowed_values))
    if col.default is not None:
        details.append(f'se assente usa "{col.default}"')
    if col.context:
        details.append(
            "dato di pagina (es. intestazione): riportalo in page_context; nelle righe lascialo \"\" "
            "se è uguale a page_context, compilalo solo se per quella riga è diverso"
        )
    if details:
        parts.append(f" ({'; '.join(details)})")
    return "".join(parts)


def build_page_schema(spec: IndexingSpec) -> dict[str, Any]:
    keys = spec.json_keys()
    row_props = {
        keys[c.name]: {"type": "string", "description": c.description or c.name} for c in spec.columns
    }
    properties: dict[str, Any] = {
        "page_summary": {"type": "string"},
        "rows": {
            "type": "array",
            "items": {"type": "object", "properties": row_props, "required": list(row_props)},
        },
        "notes": {"type": "string"},
    }
    required = ["page_summary", "rows", "notes"]

    if spec.context_columns:
        ctx_props = {keys[c.name]: {"type": "string"} for c in spec.context_columns}
        properties["page_context"] = {
            "type": "object",
            "properties": ctx_props,
            "required": list(ctx_props),
        }
        required.insert(1, "page_context")

    return {"type": "object", "properties": properties, "required": required}


class PromptBuilder:
    """Prepara una volta le parti fisse del prompt; poi produce il testo per ogni pagina."""

    def __init__(self, spec: IndexingSpec, primer_text: str, total_pages: int):
        self.spec = spec
        self.total_pages = total_pages
        self.schema = build_page_schema(spec)
        keys = spec.json_keys()

        mappings = ""
        for name, m in spec.mappings.items():
            lines = "\n".join(f"  - {k} → {v}" for k, v in m.values.items())
            title = f"{name}: {m.description}" if m.description else name
            mappings += f"\n### Tabella di conversione — {title}\n{lines}\n"

        instructions = (
            f"\n## ISTRUZIONI DELL'UTENTE (hanno la precedenza sulle regole generali)\n"
            f"{spec.instructions.strip()}\n"
            if spec.instructions.strip()
            else ""
        )

        example = spec.example_text()
        examples = (
            "\n## ESEMPIO DI RISULTATO ATTESO (CSV)\n"
            "Righe d'esempio da un indice già fatto: usale come riferimento di formato e stile, "
            "NON copiarne i valori.\n```csv\n" + example + "\n```\n"
            if example
            else ""
        )

        context_rule = (
            "- In \"page_context\" riporta i valori delle colonne di contesto validi per tutta la "
            "pagina (es. intestazione), se visibili; \"\" se non compaiono in questa pagina. Nelle righe "
            "lascia vuote queste colonne quando il valore è quello di page_context: il sistema le "
            "compila automaticamente (così la risposta resta breve).\n"
            if spec.context_columns
            else ""
        )

        self._fixed = dict(
            primer=primer_text.strip() or "(nessun primer disponibile)",
            row=spec.row.strip(),
            columns="\n".join(_describe_column(c, keys[c.name]) for c in spec.columns),
            mappings=mappings,
            instructions=instructions,
            examples=examples,
            context_rule=context_rule,
            total_pages=total_pages,
        )
        self._template = load_prompt("indexing_page")

    def page_prompt(self, page_number: int, page_text: str = "") -> str:
        text_block = (
            "\n## TESTO ESTRATTO DAL PDF PER QUESTA PAGINA\n"
            "Testo nativo della pagina, estratto automaticamente. Perde la disposizione di tabelle e "
            "colonne e può contenere piccoli errori: usa l'IMMAGINE per capire struttura e associazioni, "
            "e questo testo per trascrivere con esattezza codici, numeri e sigle quando ci sono.\n"
            "```text\n" + page_text.strip() + "\n```\n"
            if page_text.strip()
            else ""
        )
        return render_prompt(self._template, page_number=page_number, page_text=text_block, **self._fixed)
