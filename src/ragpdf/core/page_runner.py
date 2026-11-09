"""Esecuzione parallela di un'operazione su molte pagine, con manifest.

Sia la descrizione delle pagine (RAG) sia l'indicizzazione fanno la stessa cosa:
per ogni pagina chiamano Gemini e salvano un risultato. Questo modulo raccoglie
la parte comune:

- esegue il lavoro in parallelo (un thread per chiave API, di default);
- aggiorna il manifest dopo ogni pagina (fatta / fallita);
- si ferma in modo ordinato se le quote sono esaurite o se troppe pagine di fila
  falliscono (tipico di un problema generale, es. nome del modello sbagliato:
  inutile sprecare richieste su tutte le pagine);
- con Ctrl+C si interrompe senza perdere le pagine già completate.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from tqdm import tqdm

from ragpdf.core.gemini import GeminiNotConfiguredError, QuotaExhaustedError
from ragpdf.core.manifest import Manifest

MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class RunReport:
    requested: int = 0
    succeeded: int = 0
    failed: dict[int, str] = field(default_factory=dict)
    not_attempted: int = 0
    stopped_reason: str | None = None

    @property
    def completed(self) -> bool:
        return self.stopped_reason is None and not self.failed and self.not_attempted == 0


class _Stop(Exception):
    pass


@dataclass
class RunControl:
    """Collegamento con un'interfaccia: avanzamento e richiesta di annullamento.

    ``progress(fatte, totali, messaggio)`` viene chiamata dopo ogni pagina; ``cancel``
    è un evento che l'interfaccia può impostare per fermare il lavoro in modo ordinato
    (le pagine in corso terminano, le altre non partono).
    """

    progress: Callable[[int, int, str], None] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def report(self, done: int, total: int, message: str = "") -> None:
        if self.progress is not None:
            self.progress(done, total, message)


def run_pages(
    pages: list[int],
    worker: Callable[[int], None],
    manifest: Manifest,
    parallelism: int,
    description: str,
    show_progress: bool = True,
    control: RunControl | None = None,
) -> RunReport:
    report = RunReport(requested=len(pages))
    if not pages:
        return report
    control = control or RunControl()

    stop = threading.Event()
    lock = threading.Lock()
    consecutive_failures = 0

    def task(n: int) -> None:
        nonlocal consecutive_failures
        if stop.is_set() or control.cancel.is_set():
            raise _Stop()
        try:
            worker(n)
        except (QuotaExhaustedError, GeminiNotConfiguredError) as e:
            # Non è colpa della pagina: non si conta come tentativo fallito.
            with lock:
                if report.stopped_reason is None:
                    report.stopped_reason = str(e)
            stop.set()
            raise _Stop() from e
        except Exception as e:
            message = f"{type(e).__name__}: {e}"
            manifest.mark_failed(n, message)
            with lock:
                report.failed[n] = message
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES and report.stopped_reason is None:
                    report.stopped_reason = (
                        f"{MAX_CONSECUTIVE_FAILURES} pagine di fila sono fallite: probabile problema "
                        f"generale (chiave, modello, rete). Ultimo errore: {message}"
                    )
                    stop.set()
            raise
        else:
            manifest.mark_done(n)
            with lock:
                report.succeeded += 1
                consecutive_failures = 0

    executor = ThreadPoolExecutor(max_workers=max(1, parallelism))
    try:
        futures = {executor.submit(task, n): n for n in pages}
        done = 0
        control.report(0, len(pages), description)
        with tqdm(total=len(pages), desc=description, unit="pag", disable=not show_progress) as bar:
            for future in as_completed(futures):
                n = futures[future]
                try:
                    future.result()
                except _Stop:
                    with lock:
                        report.not_attempted += 1
                except Exception:
                    if show_progress:
                        tqdm.write(f"  Pagina {n}: {report.failed.get(n, 'errore')}")
                bar.update(1)
                done += 1
                control.report(done, len(pages), description)
    except KeyboardInterrupt:
        stop.set()
        report.stopped_reason = "interrotto dall'utente (Ctrl+C)"
        executor.shutdown(wait=True, cancel_futures=True)
        report.not_attempted = report.requested - report.succeeded - len(report.failed)
        return report
    finally:
        executor.shutdown(wait=True)

    if control.cancel.is_set() and report.stopped_reason is None and report.not_attempted:
        report.stopped_reason = "annullato dall'utente"
    return report
