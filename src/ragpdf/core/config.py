"""Configurazione dell'applicazione, letta da ``configs/default.yaml``.

Ogni sezione del file YAML corrisponde a un modello Pydantic: i valori vengono
validati all'avvio e, se il file contiene una chiave sconosciuta (tipicamente un
errore di battitura), il programma lo segnala subito invece di ignorarla.

Tutti i campi hanno un valore di default: il file YAML serve solo a cambiare
quelli che interessano.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ragpdf.core.paths import DEFAULT_CONFIG_FILE, resolve_path


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeminiSettings(_Section):
    model: str = Field(
        "gemini-3.6-flash",
        description="Modello per la lettura delle pagine (vision) e l'estrazione strutturata.",
    )
    text_model: str = Field(
        "gemini-3.6-flash",
        description="Modello per le risposte testuali e la generazione del primer.",
    )
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    media_resolution: Literal["low", "medium", "high"] | None = Field(
        None,
        description="Dettaglio con cui Gemini guarda immagini e PDF. Vuoto = default del modello.",
    )
    requests_per_minute_per_key: float = Field(
        10, gt=0, description="Ritmo massimo di richieste per ciascuna chiave API."
    )
    max_retries: int = Field(6, ge=1, description="Tentativi per singola richiesta.")
    quota_cooldown_seconds: int = Field(
        60, ge=1, description="Pausa di una chiave dopo un errore di quota al minuto (429)."
    )


class RenderSettings(_Section):
    dpi: int = Field(200, ge=72, le=600, description="Risoluzione delle immagini di pagina.")


class IndexingSettings(_Section):
    input: Literal["image+text", "image", "pdf"] = Field(
        "image+text",
        description="Cosa ricevere per ogni pagina: immagine + testo estratto, solo immagine, o PDF.",
    )
    max_workers: int | None = Field(
        None, ge=1, description="Richieste parallele. Vuoto = una per chiave API."
    )
    max_page_attempts: int = Field(
        3, ge=1, description="Dopo quanti fallimenti una pagina non viene più ritentata."
    )


class RagSettings(_Section):
    collection_name: str = "documents"
    top_k: int = Field(4, ge=1)


class Config(_Section):
    workspace_dir: str = Field("data", description="Cartella dei dati dell'utente.")
    gemini: GeminiSettings = GeminiSettings()
    render: RenderSettings = RenderSettings()
    indexing: IndexingSettings = IndexingSettings()
    rag: RagSettings = RagSettings()

    @property
    def workspace_path(self) -> Path:
        return resolve_path(self.workspace_dir)


class ConfigError(Exception):
    """Configurazione mancante o non valida, con messaggio leggibile."""


def load_config(config_path: str | Path | None = None) -> Config:
    """Carica la configurazione. Se il file non esiste usa i valori di default."""
    target = DEFAULT_CONFIG_FILE if config_path is None else resolve_path(config_path)

    if not target.exists():
        if config_path is not None:
            raise ConfigError(f"File di configurazione non trovato: {target}")
        return Config()

    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Il file {target.name} non è YAML valido:\n{e}") from e

    try:
        return Config.model_validate(data)
    except ValidationError as e:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(f"Valori non validi in {target.name}:\n{problems}") from e
