"""Client Gemini *simulato*: stesse funzioni del vero, nessuna chiamata di rete.

Serve a due cose:

- **test automatici**: si può decidere esattamente cosa "risponde" il modello
  e verificare come reagisce il programma;
- **prova senza chiavi**: con la variabile d'ambiente ``RAGPDF_FAKE_GEMINI=1``
  la CLI usa questo client, così si può provare tutto il flusso (aggiunta del
  documento, primer, indicizzazione, export) senza consumare quota.

Le risposte di default sono segnaposto riconoscibili (``[simulato] ...``). Per
risposte personalizzate si passa un ``responder``: una funzione che riceve la
richiesta e restituisce la risposta.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FakeRequest:
    kind: str                     # "text" | "json" | "pdf"
    prompt: str
    images: list[Path] = field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    pdf_path: Path | None = None
    model: str | None = None


def _placeholder_from_schema(schema: dict[str, Any], label: str = "valore") -> Any:
    """Costruisce un oggetto d'esempio che rispetta uno JSON Schema semplice."""
    t = schema.get("type")
    if t == "object":
        return {
            name: _placeholder_from_schema(sub, name)
            for name, sub in schema.get("properties", {}).items()
        }
    if t == "array":
        return [_placeholder_from_schema(schema.get("items", {}), label)]
    if t == "boolean":
        return True
    if t in ("integer", "number"):
        return 1
    return f"[simulato] {label}"


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

    @property
    def parallelism(self) -> int:
        return self._parallelism

    def _handle(self, request: FakeRequest, default: Callable[[], Any]) -> Any:
        with self._lock:
            self.requests.append(request)
        if self.responder is not None:
            return self.responder(request)
        return default()

    def generate_text(self, prompt: str, images: Sequence[Path] = (), model: str | None = None) -> str:
        req = FakeRequest("text", prompt, list(images), model=model)
        return self._handle(req, lambda: "[simulato] Risposta generata senza chiamare Gemini.")

    def generate_json(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[Path] = (),
        model: str | None = None,
    ) -> Any:
        req = FakeRequest("json", prompt, list(images), json_schema=json_schema, model=model)
        return self._handle(req, lambda: _placeholder_from_schema(json_schema))

    def generate_text_from_pdf(self, prompt: str, pdf_path: Path, model: str | None = None) -> str:
        req = FakeRequest("pdf", prompt, pdf_path=Path(pdf_path), model=model)
        return self._handle(
            req,
            lambda: (
                f"# Primer — {Path(pdf_path).stem} [simulato]\n\n"
                "Bozza generata dal client simulato: sostituiscila con un primer reale.\n"
            ),
        )
