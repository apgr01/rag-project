"""Bozza di una specifica di indicizzazione scritta da Gemini.

L'utente descrive a parole cosa vuole ottenere ("una riga per ogni codice, con
marca, modello e codice originale; se manca il codice originale lascia vuoto")
e, se li ha, fornisce:

- un **CSV d'esempio** di un indice già fatto (per nomi e ordine delle colonne);
- un **documento** del workspace: Gemini ne vede il primer e alcune pagine.

Gemini restituisce la specifica in YAML, che viene validata prima di essere
salvata. Se non è valida, si chiede a Gemini di correggerla mostrandogli gli
errori (un solo tentativo in più). Il risultato è sempre una *bozza*: va riletta.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from ragpdf.core.documents import Document
from ragpdf.core.gemini import LLMClient
from ragpdf.core.paths import EXAMPLES_DIR
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import render_pages
from ragpdf.indexing.spec import IndexingSpec, SpecError, load_spec

MAX_SAMPLE_PAGES = 4


@dataclass
class DraftInput:
    name: str
    request: str
    example_csv: Path | None = None
    doc: Document | None = None
    primer_text: str = ""
    pages: list[int] | None = None


def default_sample_pages(page_count: int) -> list[int]:
    """Qualche pagina centrale: di solito il contenuto "tipico" del documento."""
    if page_count <= MAX_SAMPLE_PAGES:
        return list(range(1, page_count + 1))
    middle = page_count // 2
    return [middle - 1, middle, middle + 1, middle + 2][:MAX_SAMPLE_PAGES]


def _strip_fences(text: str) -> str:
    match = re.search(r"```(?:ya?ml)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    return (match.group(1) if match else text).strip() + "\n"


def _build_prompt(inp: DraftInput, example_text: str) -> str:
    template = (EXAMPLES_DIR / "specs" / "base.yaml").read_text(encoding="utf-8")
    example = (
        "\n## CSV D'ESEMPIO FORNITO DALL'UTENTE (prime righe)\n```csv\n" + example_text + "\n```\n"
        if example_text
        else ""
    )
    primer = (
        "\n## COM'È FATTO IL DOCUMENTO (primer)\n" + inp.primer_text.strip() + "\n"
        if inp.primer_text.strip()
        else ""
    )
    pages_note = (
        f"\nIn allegato trovi le immagini di alcune pagine del documento (pagine {', '.join(map(str, inp.pages))}).\n"
        if inp.pages
        else ""
    )
    return render_prompt(
        load_prompt("spec_draft"),
        template=template.strip(),
        request=inp.request.strip(),
        example=example,
        primer=primer,
        pages_note=pages_note,
        name=inp.name,
    )


def draft_spec(inp: DraftInput, client: LLMClient, target: Path, dpi: int = 120) -> IndexingSpec:
    """Genera, valida e salva la bozza in ``target``. Restituisce la specifica caricata."""
    example_text = ""
    if inp.example_csv is not None:
        if not inp.example_csv.exists():
            raise FileNotFoundError(f"CSV d'esempio non trovato: {inp.example_csv}")
        lines = inp.example_csv.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        example_text = "\n".join(lines[:21])

    images: list[Path] = []
    if inp.doc is not None:
        inp.pages = inp.pages or default_sample_pages(inp.doc.info.page_count)
        rendered = render_pages(inp.doc.pdf_path, inp.doc.pages_dir, dpi, inp.pages, show_progress=False)
        images = [rendered[p] for p in inp.pages]
    else:
        inp.pages = None

    prompt = _build_prompt(inp, example_text)
    text = _strip_fences(client.generate_text(prompt, images=images))

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        spec = _save_and_load(text, inp, target)
    except SpecError as first_error:
        retry = (
            prompt
            + "\n\n## LA TUA BOZZA PRECEDENTE NON ERA VALIDA\n```yaml\n"
            + text
            + "\n```\nErrori:\n"
            + str(first_error)
            + "\n\nCorreggila e rispondi di nuovo solo con il YAML."
        )
        text = _strip_fences(client.generate_text(retry, images=images))
        spec = _save_and_load(text, inp, target)
    return spec


def _save_and_load(text: str, inp: DraftInput, target: Path) -> IndexingSpec:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        target.write_text(text, encoding="utf-8")  # salvata così com'è, per la correzione a mano
        raise SpecError(f"YAML non valido: {e}") from e
    if not isinstance(data, dict):
        target.write_text(text, encoding="utf-8")
        raise SpecError("La risposta non è una specifica (atteso un insieme di campi YAML).")

    data["name"] = inp.name
    if inp.example_csv is not None:
        # Il CSV d'esempio viene copiato accanto alla specifica e usato come riferimento.
        copy = target.with_name(f"{inp.name}_esempio.csv")
        shutil.copyfile(inp.example_csv, copy)
        data["examples"] = {**(data.get("examples") or {}), "csv": copy.name}

    header = (
        "# Bozza generata automaticamente da 'ragpdf spec draft'.\n"
        "# Rileggila e correggila prima dell'uso; controllala con: ragpdf spec check "
        f"{inp.name}\n\n"
    )
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    target.write_text(header + body, encoding="utf-8")
    return load_spec(target)
