"""Manifest: lo stato di avanzamento di un'elaborazione pagina per pagina.

Ogni fase che chiama Gemini su tutte le pagine (descrizione RAG, indicizzazione)
tiene un ``manifest.json`` con lo stato di ogni pagina::

    {
      "total_pages": 120,
      "meta": {"fingerprint": "...", ...},
      "pages": {
        "1": {"status": "done",    "attempts": 1, "error": null, "updated_at": "..."},
        "2": {"status": "failed",  "attempts": 2, "error": "...", "updated_at": "..."},
        "3": {"status": "pending", "attempts": 0, "error": null, "updated_at": null}
      }
    }

Grazie al manifest il lavoro può essere interrotto e ripreso in qualsiasi
momento: al rilancio si elaborano solo le pagine non ancora completate.

Il manifest è condiviso tra più thread, quindi ogni modifica avviene sotto lock
e viene salvata subito su disco (in modo atomico).
"""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path
from typing import Any

from ragpdf.core.fileio import read_json, utc_now, write_json

PENDING, DONE, FAILED = "pending", "done", "failed"


class Manifest:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path
        self.data = data
        self._lock = threading.Lock()

    @classmethod
    def load_or_create(cls, path: Path, total_pages: int, meta: dict[str, Any] | None = None) -> Manifest:
        if path.exists():
            data = read_json(path)
            # Il PDF non cambia (è identificato dal suo hash), ma per sicurezza
            # si aggiungono eventuali pagine mancanti.
            for n in range(1, total_pages + 1):
                data["pages"].setdefault(str(n), cls._empty_page())
            data["total_pages"] = total_pages
            return cls(path, data)

        data = {
            "total_pages": total_pages,
            "meta": meta or {},
            "pages": {str(n): cls._empty_page() for n in range(1, total_pages + 1)},
        }
        manifest = cls(path, data)
        manifest.save()
        return manifest

    @staticmethod
    def _empty_page() -> dict[str, Any]:
        return {"status": PENDING, "attempts": 0, "error": None, "updated_at": None}

    @property
    def meta(self) -> dict[str, Any]:
        return self.data.setdefault("meta", {})

    def page(self, n: int) -> dict[str, Any]:
        return self.data["pages"][str(n)]

    def reset(self, meta: dict[str, Any] | None = None) -> None:
        """Riporta tutte le pagine allo stato iniziale (usato con --force)."""
        with self._lock:
            for key in self.data["pages"]:
                self.data["pages"][key] = self._empty_page()
            if meta is not None:
                self.data["meta"] = meta
            self._save_unlocked()

    def pages_to_process(self, max_attempts: int, only: list[int] | None = None) -> list[int]:
        """Pagine da (ri)elaborare: in attesa, oppure fallite con tentativi residui."""
        selected = set(only) if only is not None else None
        result = []
        for key, info in self.data["pages"].items():
            n = int(key)
            if selected is not None and n not in selected:
                continue
            if info["status"] == PENDING or (
                info["status"] == FAILED and info["attempts"] < max_attempts
            ):
                result.append(n)
        return sorted(result)

    def mark_done(self, n: int) -> None:
        self._update(n, DONE, None)

    def mark_failed(self, n: int, error: str) -> None:
        self._update(n, FAILED, error)

    def _update(self, n: int, status: str, error: str | None) -> None:
        with self._lock:
            info = self.data["pages"][str(n)]
            info.update(
                status=status,
                attempts=info["attempts"] + 1,
                error=error,
                updated_at=utc_now(),
            )
            self._save_unlocked()

    def counts(self) -> Counter:
        return Counter(info["status"] for info in self.data["pages"].values())

    def done_pages(self) -> set[int]:
        return {int(k) for k, v in self.data["pages"].items() if v["status"] == DONE}

    def failed_pages(self) -> dict[int, str]:
        return {
            int(k): v["error"] or "" for k, v in self.data["pages"].items() if v["status"] == FAILED
        }

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        write_json(self.path, self.data)
