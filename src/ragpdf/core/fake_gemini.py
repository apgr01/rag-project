"""Client Gemini *simulato*: stesse funzioni del vero, nessuna chiamata di rete.

Serve a due cose:

- **test automatici**: si può decidere esattamente cosa "risponde" il modello
  e verificare come reagisce il programma;
- **prova senza chiavi**: con la variabile d'ambiente ``RAGPDF_FAKE_GEMINI=1``
  la CLI usa questo client, così si può provare tutto il flusso (aggiunta del
  documento, primer, indicizzazione, export) senza consumare quota.

Le risposte di default sono segnaposto riconoscibili (``[simulato] ...``),
costruiti in modo da rispettare lo schema richiesto. Per risposte personalizzate
si passa un ``responder``: una funzione che riceve la richiesta e restituisce la
risposta.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FakeRequest:
    kind: str                     # "text" | "json"
    prompt: str
    images: list[Path] = field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    pdf: Path | None = None
    model: str | None = None
    pdf_pages: int | None = None  # pagine del PDF allegato (letto al momento della richiesta)


_SIMULATED_SPEC = """row: Una riga per ogni codice presente nella pagina (simulato).
columns:
  - name: CODICE
    description: Codice articolo.
  - name: DESCRIZIONE
    description: Descrizione breve.
"""


def _placeholder_from_schema(schema: dict[str, Any], label: str = "valore") -> Any:
    """Costruisce un oggetto d'esempio che rispetta uno JSON Schema semplice.

    I campi che finiscono in ``_yaml`` / ``_markdown`` ricevono contenuti minimi ma
    validi, così anche la sessione guidata funziona in modalità simulata.
    """
    t = schema.get("type")
    if t == "string" and label.endswith("_yaml"):
        return _SIMULATED_SPEC
    if t == "string" and label.endswith("_markdown"):
        return "# Primer [simulato]\n\nContenuto generato senza chiamare Gemini.\n"
    if t == "object":
        return {
            name: _placeholder_from_schema(sub, name)
            for name, sub in schema.get("properties", {}).items()
        }
    if t == "array":
        if schema.get("items", {}).get("type") == "integer":
            return []
        return [_placeholder_from_schema(schema.get("items", {}), label)]
    if t == "boolean":
        return True
    if t in ("integer", "number"):
        return 1
    return f"[simulato] {label}"


def _count_pages(pdf: Path | None) -> int | None:
    if pdf is None:
        return None
    try:
        import pymupdf

        with pymupdf.open(pdf) as doc:
            return len(doc)
    except Exception:
        return None


class FakeGeminiClient:
    def __init__(
        self,
        responder: Callable[[FakeRequest], Any] | None = None,
        parallelism: int = 2,
    ):
        self.responder = responder
        self._parallelism = parallelism
        self.requests: list[FakeRequest] = []
        self._lock = threading.Lock()
        from ragpdf.core.gemini import Usage

        self.usage = Usage()

    @property
    def parallelism(self) -> int:
        return self._parallelism

    def _handle(self, request: FakeRequest, default: Callable[[], Any]) -> Any:
        # Il PDF allegato può essere un file temporaneo: lo si legge subito.
        request.pdf_pages = _count_pages(request.pdf)
        with self._lock:
            self.requests.append(request)
            self.usage.requests += 1
        if self.responder is not None:
            return self.responder(request)
        return default()

    def generate_text(
        self, prompt: str, images: Sequence[Path] = (), pdf: Path | None = None, model: str | None = None
    ) -> str:
        req = FakeRequest("text", prompt, list(images), pdf=pdf, model=model)
        if pdf is not None:
            default = "# Primer [simulato]\n\nBozza generata dal client simulato: sostituiscila con un primer reale.\n"
        else:
            default = "[simulato] Risposta generata senza chiamare Gemini."
        return self._handle(req, lambda: default)

    def generate_json(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[Path] = (),
        pdf: Path | None = None, model: str | None = None,
    ) -> Any:
        req = FakeRequest("json", prompt, list(images), json_schema=json_schema, pdf=pdf, model=model)
        return self._handle(req, lambda: _placeholder_from_schema(json_schema))
