import os
import itertools
import threading
import time
from pathlib import Path
from typing import Type, TypeVar, Optional, List, Union
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pydantic import BaseModel

from common.paths import ENV_FILE

# Carica espressamente il file .env dalla radice del progetto
load_dotenv(dotenv_path=ENV_FILE)

T = TypeVar("T", bound=BaseModel)

class GeminiClient:
    """
    Wrapper centralizzato per le chiamate a Google Gemini.
    Carica ed ordina le chiavi e gestisce la concorrenza in modo Thread-Safe.
    """

    def __init__(self, api_keys: Optional[List[str]] = None):
        if api_keys:
            self.api_keys = api_keys
        else:
            self.api_keys = self._load_keys_from_env()

        if not self.api_keys:
            raise ValueError(f"Nessuna API Key trovata. Verifica il file .env in {ENV_FILE}")

        self._key_cycle = itertools.cycle(self.api_keys)
        self._current_key = next(self._key_cycle)
        
        # Lock per evitare che più thread ruotino la chiave o confliggano
        self._lock = threading.Lock()

    def _load_keys_from_env(self) -> List[str]:
        keys = []
        
        raw_keys = os.getenv("GEMINI_API_KEYS")
        if raw_keys:
            keys.extend([k.strip() for k in raw_keys.split(",") if k.strip()])

        key_vars = []
        for env_name, env_val in os.environ.items():
            if env_name.startswith("GEMINI_API_KEY") and env_name != "GEMINI_API_KEYS":
                if env_val and env_val.strip():
                    key_vars.append((env_name, env_val.strip()))

        key_vars.sort(key=lambda x: x[0])
        for _, val in key_vars:
            if val not in keys:
                keys.append(val)

        return keys

    def _rotate_key_if_needed(self, failed_key: str):
        with self._lock:
            # Ruota la chiave solo se nessun altro thread l'ha già cambiata nel frattempo
            if self._current_key == failed_key:
                self._current_key = next(self._key_cycle)
            return self._current_key

    def _call_with_fallback(self, action_callable):
        last_exception = None
        for attempt in range(len(self.api_keys)):
            # 1. Prendi la chiave corrente in modo sicuro
            with self._lock:
                current_key = self._current_key
            
            # 2. Crea un client LOCALE per questo thread (non condiviso!)
            local_client = genai.Client(api_key=current_key)
            
            try:
                # 3. Esegui l'azione passando il client locale
                return action_callable(local_client)
            
            except Exception as e:
                last_exception = e
                err_msg = str(e).lower()
                
                if "429" in err_msg or "quota" in err_msg or "resourceexhausted" in err_msg:
                    print(f"⚠️ Quota superata. Cambio API Key (tentativo {attempt + 1}/{len(self.api_keys)})...")
                    self._rotate_key_if_needed(current_key)
                
                elif "503" in err_msg or "unavailable" in err_msg:
                    print(f"⏳ API sovraccarica (503). Attendo 5 secondi...")
                    time.sleep(5)
                
                else:
                    raise e
                    
        raise last_exception

    def generate_text(self, prompt: str, model_name: str = "gemini-2.5-flash", temperature: float = 0.2) -> str:
        def _action(client):
            chat = client.chats.create(
                model=model_name,
                config=types.GenerateContentConfig(temperature=temperature)
            )
            response = chat.send_message(prompt)
            return response.text
        return self._call_with_fallback(_action)

    def generate_with_vision(self, prompt: str, image_path: str, model_name: str = "gemini-2.5-flash", temperature: float = 0.2) -> str:
        img_p = Path(image_path)
        if not img_p.exists():
            raise FileNotFoundError(f"Immagine non trovata: {image_path}")

        def _action(client):
            image_bytes = img_p.read_bytes()
            chat = client.chats.create(
                model=model_name,
                config=types.GenerateContentConfig(temperature=temperature)
            )
            response = chat.send_message(
                [prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")]
            )
            return response.text
        return self._call_with_fallback(_action)

    def generate_structured(self, prompt: str, response_schema: Type[T], image_path: Optional[str] = None, model_name: str = "gemini-2.5-flash", temperature: float = 0.1) -> T:
        def _action(client):
            message_parts = [prompt]
            if image_path:
                img_p = Path(image_path)
                if not img_p.exists():
                    raise FileNotFoundError(f"Immagine non trovata: {image_path}")
                message_parts.append(types.Part.from_bytes(data=img_p.read_bytes(), mime_type="image/png"))

            chat = client.chats.create(
                model=model_name,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                    response_schema=response_schema
                )
            )
            response = chat.send_message(message_parts)
            return response_schema.model_validate_json(response.text)
        return self._call_with_fallback(_action)