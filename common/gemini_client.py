"""
common/gemini_client.py

Gestione centralizzata delle chiavi API Gemini multiple, con rotazione
automatica quando una chiave esaurisce la quota (errore 429).

Usato sia da ingestion/describe_pages.py sia da rag/query.py, cosi' la
logica di rotazione e' scritta e mantenuta in un solo posto.
"""

import os
import re
from typing import Callable

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors


class QuotaExhausted(Exception):
    """Segnala che TUTTE le chiavi disponibili hanno esaurito la quota."""
    pass


class KeyManager:
    """Gestisce l'elenco delle API Key disponibili e ruota la chiave quando una va in quota esaurita."""

    def __init__(self):
        load_dotenv()
        self.keys: list[tuple[str, str]] = []  # [(nome_variabile, valore_chiave), ...]
        self.exhausted_keys: set[str] = set()

        # 1. Chiave singola classica
        single_key = os.environ.get("GEMINI_API_KEY")
        if single_key:
            self.keys.append(("GEMINI_API_KEY", single_key.strip()))

        # 2. Chiavi numerate (GEMINI_API_KEY_01, GEMINI_API_KEY_02, ...)
        key_pattern = re.compile(r"^GEMINI_API_KEY_\d+$")
        env_keys = sorted(k for k in os.environ.keys() if key_pattern.match(k))

        for k in env_keys:
            val = os.environ.get(k, "").strip()
            if val and (k, val) not in self.keys:
                self.keys.append((k, val))

        if not self.keys:
            raise RuntimeError(
                "Nessuna API Key trovata nel file .env.\n"
                "Definisci GEMINI_API_KEY oppure GEMINI_API_KEY_01, GEMINI_API_KEY_02..."
            )

        self.current_index = 0

    @property
    def current_key_name(self) -> str:
        return self.keys[self.current_index][0]

    def get_client(self) -> genai.Client:
        _, api_key = self.keys[self.current_index]
        return genai.Client(api_key=api_key)

    def mark_current_exhausted(self) -> bool:
        """Marca la chiave corrente come esaurita e passa alla successiva disponibile.
        Ritorna True se ce n'e' un'altra, False se sono finite tutte."""
        key_name = self.current_key_name
        self.exhausted_keys.add(key_name)

        for i in range(len(self.keys)):
            next_idx = (self.current_index + 1 + i) % len(self.keys)
            next_name = self.keys[next_idx][0]
            if next_name not in self.exhausted_keys:
                self.current_index = next_idx
                return True

        return False


def generate_with_rotation(key_manager: KeyManager, log: Callable[[str], None] = print, **kwargs):
    """
    Wrapper attorno a client.models.generate_content(**kwargs): se la chiave
    corrente va in quota esaurita (429), ruota automaticamente alla successiva
    e ripete la STESSA richiesta, senza che il chiamante debba gestirlo.

    Solleva QuotaExhausted solo quando anche l'ultima chiave disponibile
    e' esaurita.

    'log' e' la funzione usata per stampare i messaggi di cambio chiave —
    passa tqdm.write invece di print se stai iterando dentro una barra tqdm,
    cosi' non rompi la progress bar.
    """
    while True:
        client = key_manager.get_client()
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.ClientError as e:
            if e.code != 429:
                raise
            log(f"⚠️  Quota esaurita per {key_manager.current_key_name}.")
            if key_manager.mark_current_exhausted():
                log(f"🔄 Cambio automatico alla chiave: {key_manager.current_key_name}...")
                continue
            raise QuotaExhausted(
                "TUTTE le API Key disponibili nel file .env hanno esaurito la quota."
            ) from e