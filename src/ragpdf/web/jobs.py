"""Lavori in background per l'interfaccia web.

Le operazioni lunghe (chiamate a Gemini, anteprime, indicizzazione completa) non possono
bloccare il server: vengono eseguite in un thread separato come *lavori*. La pagina web
ne chiede periodicamente lo stato (avanzamento, messaggio, esito) e può chiederne
l'annullamento.

Regola semplice: **un solo lavoro alla volta per documento**, così non si possono
lanciare per errore due indicizzazioni dello stesso catalogo.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ragpdf.core.fileio import utc_now
from ragpdf.core.page_runner import RunControl

RUNNING, DONE, ERROR, CANCELLED = "in_corso", "completato", "errore", "annullato"


class JobConflictError(Exception):
    """C'è già un lavoro in corso per lo stesso documento."""


@dataclass
class Job:
    id: str
    doc_id: str
    kind: str
    label: str
    status: str = RUNNING
    done: int = 0
    total: int = 0
    message: str = ""
    error: str | None = None
    result: Any = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    control: RunControl = field(default_factory=RunControl)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.control.cancel.is_set(),
        }


class JobManager:
    def __init__(self, run_inline: bool = False):
        """``run_inline=True`` esegue i lavori subito, nello stesso thread (per i test)."""
        self.run_inline = run_inline
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active_for(self, doc_id: str) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.doc_id == doc_id and job.status == RUNNING:
                    return job
        return None

    def latest_for(self, doc_id: str) -> Job | None:
        jobs = [j for j in self._jobs.values() if j.doc_id == doc_id]
        return max(jobs, key=lambda j: j.started_at) if jobs else None

    def submit(self, doc_id: str, kind: str, label: str, work: Callable[[Job], Any]) -> Job:
        """Avvia ``work(job)`` in background. ``work`` può aggiornare ``job.message``."""
        with self._lock:
            for other in self._jobs.values():
                if other.doc_id == doc_id and other.status == RUNNING:
                    raise JobConflictError(
                        f"C'è già un'operazione in corso su questo documento: {other.label}."
                    )
            job = Job(id=uuid.uuid4().hex[:12], doc_id=doc_id, kind=kind, label=label)

            def progress(done: int, total: int, message: str) -> None:
                job.done, job.total = done, total
                if message:
                    job.message = message

            job.control.progress = progress
            self._jobs[job.id] = job

        def run() -> None:
            try:
                job.result = work(job)
                job.status = CANCELLED if job.control.cancel.is_set() else DONE
            except Exception as e:  # l'errore viene mostrato nella pagina
                job.status = ERROR
                job.error = str(e) or type(e).__name__
                job.message = "Operazione non riuscita"
                job.result = {"traceback": traceback.format_exc(limit=5)}
            finally:
                job.finished_at = utc_now()

        if self.run_inline:
            run()
        else:
            threading.Thread(target=run, name=f"job-{kind}-{job.id}", daemon=True).start()
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job is not None and job.status == RUNNING:
            job.control.cancel.set()
            job.message = "Annullamento in corso: termino le pagine già avviate..."
        return job
