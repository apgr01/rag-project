"""Client Gemini: un punto unico per tutte le chiamate al modello.

Cosa aggiunge rispetto a usare direttamente l'SDK ``google-genai``:

**Più chiavi API, usate in parallelo.** Le chiavi (``GEMINI_API_KEY_01``,
``_02``, ... nel file ``.env``) formano un *pool*. Ogni richiesta prende la
chiave libera prima delle altre.

**Ritmo controllato per chiave.** Ogni chiave non riceve più di
``requests_per_minute_per_key`` richieste al minuto, anche con molti thread:
il limite è condiviso, non per thread.

**Errori gestiti per tipo** (dal codice HTTP, non cercando parole nel testo):

- ``429`` quota al minuto → la chiave va in pausa per ``quota_cooldown_seconds``
  e si riprova con un'altra;
- ``429`` quota giornaliera → la chiave viene esclusa fino alla fine dell'esecuzione;
- ``500/502/503/504`` e problemi di rete → si riprova dopo un'attesa crescente
  (*backoff esponenziale*: 2s, 4s, 8s, ...);
- altri errori (es. ``400`` richiesta non valida) → errore immediato.

Se tutte le chiavi hanno esaurito la quota giornaliera viene sollevata
:class:`QuotaExhaustedError`: chi elabora le pagine si ferma in modo ordinato e
il lavoro riprende al rilancio successivo.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values

from ragpdf.core.config import GeminiSettings
from ragpdf.core.paths import ENV_FILE

RETRYABLE_STATUS = {500, 502, 503, 504}


class GeminiError(Exception):
    """Errore non recuperabile in una chiamata a Gemini."""


class QuotaExhaustedError(GeminiError):
    """Tutte le chiavi API hanno esaurito la quota: riprovare più tardi."""


class GeminiNotConfiguredError(GeminiError):
    """Nessuna chiave API disponibile."""


class LLMClient(Protocol):
    """Interfaccia usata dal resto del programma.

    Il client vero (:class:`GeminiClient`) e quello simulato per i test
    (:class:`ragpdf.core.fake_gemini.FakeGeminiClient`) la rispettano entrambi.
    """

    @property
    def parallelism(self) -> int: ...

    def generate_text(
        self, prompt: str, images: Sequence[Path] = (), model: str | None = None
    ) -> str: ...

    def generate_json(
        self, prompt: str, json_schema: dict[str, Any], images: Sequence[Path] = (),
        model: str | None = None,
    ) -> Any: ...

    def generate_text_from_pdf(self, prompt: str, pdf_path: Path, model: str | None = None) -> str: ...


# --------------------------------------------------------------------------------------
# Chiavi API
# --------------------------------------------------------------------------------------
def load_api_keys(env_file: Path = ENV_FILE) -> list[str]:
    """Legge le chiavi da ``.env`` e dalle variabili d'ambiente.

    Formati accettati: ``GEMINI_API_KEY``, ``GEMINI_API_KEY_01``, ``_02``, ...
    oppure ``GEMINI_API_KEYS=chiave1,chiave2``. Le chiavi vengono ordinate per
    nome e i duplicati eliminati.
    """
    values: dict[str, str] = {}
    if env_file.exists():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v})
    values.update({k: v for k, v in os.environ.items() if k.startswith("GEMINI_API_KEY") and v})

    # Prima le chiavi singole in ordine di nome (_01, _02, ...), poi l'elenco GEMINI_API_KEYS.
    keys: list[str] = [
        values[name].strip()
        for name in sorted(values)
        if name.startswith("GEMINI_API_KEY") and name != "GEMINI_API_KEYS"
    ]
    if "GEMINI_API_KEYS" in values:
        keys.extend(k.strip() for k in values["GEMINI_API_KEYS"].split(","))

    unique: list[str] = []
    for k in keys:
        if k and k not in unique:
            unique.append(k)
    return unique


@dataclass
class _KeySlot:
    index: int
    key: str
    client: Any = None
    next_free_at: float = 0.0       # prima di questo istante la chiave non va usata
    paused_until: float = 0.0       # pausa per quota (inf = esclusa)


class _APIFailure(Exception):
    """Errore normalizzato: codice HTTP (o None) + messaggio."""

    def __init__(self, status: int | None, message: str):
        self.status = status
        self.message = message
        super().__init__(f"{status}: {message}" if status else message)


def _classify(exc: Exception) -> _APIFailure | None:
    """Converte le eccezioni dell'SDK in un ``_APIFailure``; ``None`` se sconosciuta."""
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        genai_errors = None

    if genai_errors is not None and isinstance(exc, genai_errors.APIError):
        return _APIFailure(getattr(exc, "code", None), str(getattr(exc, "message", "") or exc))

    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return _APIFailure(None, f"errore di rete: {exc}")
    except ImportError:  # pragma: no cover
        pass

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return _APIFailure(None, f"errore di rete: {exc}")
    return None


def _is_daily_quota(message: str) -> bool:
    text = message.lower()
    return "perday" in text or "per day" in text or "daily" in text


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------
class GeminiClient:
    def __init__(
        self,
        api_keys: Sequence[str],
        settings: GeminiSettings,
        client_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ):
        if not api_keys:
            raise GeminiNotConfiguredError(
                "Nessuna chiave API Gemini trovata. Crea il file .env partendo da "
                ".env.example e inserisci almeno una chiave (GEMINI_API_KEY_01=...)."
            )
        self.settings = settings
        self._slots = [_KeySlot(i + 1, k) for i, k in enumerate(api_keys)]
        self._client_factory = client_factory or self._default_factory
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._interval = 60.0 / settings.requests_per_minute_per_key

    @classmethod
    def from_env(cls, settings: GeminiSettings, **kwargs: Any) -> GeminiClient:
        return cls(load_api_keys(), settings, **kwargs)

    @staticmethod
    def _default_factory(key: str) -> Any:
        from google import genai

        return genai.Client(api_key=key)

    @property
    def parallelism(self) -> int:
        return len(self._slots)

    # --- gestione del pool -------------------------------------------------------
    def _acquire(self) -> _KeySlot:
        """Prenota la prossima chiave utilizzabile, attendendo se necessario."""
        while True:
            with self._lock:
                now = self._clock()
                usable = [s for s in self._slots if not math.isinf(s.paused_until)]
                if not usable:
                    raise QuotaExhaustedError(
                        "Tutte le chiavi API hanno esaurito la quota giornaliera. "
                        "Rilancia più tardi: il lavoro riprenderà da dove si è fermato."
                    )
                slot = min(usable, key=lambda s: max(s.next_free_at, s.paused_until))
                ready_at = max(slot.next_free_at, slot.paused_until, now)
                slot.next_free_at = ready_at + self._interval
                if slot.client is None:
                    slot.client = self._client_factory(slot.key)
            wait = ready_at - now
            if wait > 0:
                self._sleep(wait)
            return slot

    def _pause(self, slot: _KeySlot, daily: bool) -> None:
        with self._lock:
            if daily:
                slot.paused_until = math.inf
            else:
                slot.paused_until = self._clock() + self.settings.quota_cooldown_seconds

    def _call(self, action: Callable[[Any], Any]) -> Any:
        """Esegue ``action(client_sdk)`` con rotazione chiavi e tentativi."""
        last: _APIFailure | None = None
        errors = 0                                   # errori temporanei (contano come tentativi)
        quota_hits = 0                               # 429: si cambia chiave, non è colpa della richiesta
        max_quota_hits = max(20, 4 * len(self._slots))

        while errors < self.settings.max_retries and quota_hits < max_quota_hits:
            slot = self._acquire()
            try:
                return action(slot.client)
            except Exception as exc:
                failure = _classify(exc)
                if failure is None:
                    raise
                last = failure

                if failure.status == 429:
                    quota_hits += 1
                    daily = _is_daily_quota(failure.message)
                    self._pause(slot, daily)
                    what = "quota giornaliera esaurita" if daily else "quota al minuto superata"
                    self._log(f"[chiave {slot.index}] {what}, passo a un'altra chiave.")
                    continue

                if failure.status is None or failure.status in RETRYABLE_STATUS:
                    errors += 1
                    delay = min(2 ** errors, 60)
                    self._log(f"[chiave {slot.index}] errore temporaneo ({failure}); riprovo tra {delay}s.")
                    self._sleep(delay)
                    continue

                raise GeminiError(f"Gemini ha rifiutato la richiesta ({failure}).") from exc

        raise GeminiError(f"Richiesta fallita dopo ripetuti tentativi: {last}")

    # --- costruzione delle richieste ----------------------------------------------
    @staticmethod
    def _image_parts(images: Sequence[Path]) -> list[Any]:
        from google.genai import types

        parts = []
        for img in images:
            path = Path(img)
            if not path.exists():
                raise FileNotFoundError(f"Immagine non trovata: {path}")
            parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png"))
        return parts

    def _config(self, **extra: Any) -> Any:
        from google.genai import types

        return types.GenerateContentConfig(temperature=self.settings.temperature, **extra)

    # --- API pubblica --------------------------------------------------------------
    def generate_text(self, prompt: str, images: Sequence[Path] = (), model: str | None = None) -> str:
        contents = [*self._image_parts(images), prompt]
        model = model or (self.settings.model if images else self.settings.text_model)

        def action(client: Any) -> str:
            response = client.models.generate_content(model=model, contents=contents, config=self._config())
            return (response.text or "").strip()

        return self._call(action)

    def generate_json(
        self,
        prompt: str,
        json_schema: dict[str, Any],
        images: Sequence[Path] = (),
        model: str | None = None,
    ) -> Any:
        """Chiede una risposta JSON conforme a ``json_schema`` (output strutturato)."""
        contents = [*self._image_parts(images), prompt]
        model = model or self.settings.model
        config = self._config(response_mime_type="application/json", response_json_schema=json_schema)

        def action(client: Any) -> Any:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            text = response.text or ""
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                # Capita raramente (es. risposta troncata): la trattiamo come errore temporaneo.
                raise ConnectionError(f"risposta JSON non valida ({e})") from e

        return self._call(action)

    def _wait_until_active(self, client: Any, uploaded: Any, timeout: float = 180) -> Any:
        """Attende che Gemini abbia elaborato il file caricato (stato ACTIVE)."""
        waited = 0.0
        while True:
            state = getattr(uploaded, "state", None)
            state = str(getattr(state, "name", state) or "").upper()
            if "PROCESSING" not in state:
                if "FAILED" in state:
                    raise GeminiError(f"Gemini non è riuscito a elaborare il file {uploaded.name}.")
                return uploaded
            if waited >= timeout:
                raise ConnectionError("il file caricato non è diventato disponibile in tempo")
            self._sleep(3)
            waited += 3
            uploaded = client.files.get(name=uploaded.name)

    def generate_text_from_pdf(self, prompt: str, pdf_path: Path, model: str | None = None) -> str:
        """Invia un intero PDF (tramite la Files API di Gemini) insieme al prompt."""
        model = model or self.settings.text_model

        def action(client: Any) -> str:
            # Il file caricato appartiene alla chiave che lo carica: l'upload va
            # quindi fatto qui dentro, con il client della chiave scelta.
            uploaded = client.files.upload(file=str(pdf_path))
            uploaded = self._wait_until_active(client, uploaded)
            try:
                response = client.models.generate_content(
                    model=model, contents=[uploaded, prompt], config=self._config()
                )
                return (response.text or "").strip()
            finally:
                try:
                    client.files.delete(name=uploaded.name)
                except Exception:
                    pass  # il file scade comunque da solo dopo 48 ore

        return self._call(action)
