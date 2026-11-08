"""Piccole funzioni per l'interazione con l'utente nel terminale.

Volutamente semplici: niente colori né simboli speciali, così l'output resta
leggibile anche nel Prompt dei comandi di Windows e quando viene salvato in un
file di log.

Le domande (:func:`ask_yes_no`, :func:`choose`) funzionano solo in un terminale
interattivo; se l'input non è un terminale (es. script automatici) usano la
risposta di default senza bloccarsi.
"""

from __future__ import annotations

import sys


def setup_console() -> None:
    """Evita errori di codifica su console che non supportano tutti i caratteri."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def interactive() -> bool:
    return sys.stdin.isatty()


def title(text: str) -> None:
    print(f"\n=== {text} ===")


def info(text: str) -> None:
    print(text)


def ok(text: str) -> None:
    print(f"[OK] {text}")


def warn(text: str) -> None:
    print(f"[ATTENZIONE] {text}")


def error(text: str) -> None:
    print(f"[ERRORE] {text}", file=sys.stderr)


def hint(text: str) -> None:
    print(f"  -> {text}")


def ask_yes_no(question: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not interactive():
        return default
    suffix = "[S/n]" if default else "[s/N]"
    while True:
        answer = input(f"{question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in ("s", "si", "sì", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Rispondi 's' (sì) oppure 'n' (no).")


def choose(question: str, options: list[tuple[str, str]], default: str | None = None) -> str | None:
    """Mostra un elenco numerato e restituisce la chiave scelta.

    ``options`` è una lista di coppie ``(chiave, etichetta)``. In modalità non
    interattiva restituisce ``default``.
    """
    if not interactive():
        return default
    print(question)
    for i, (_, label) in enumerate(options, start=1):
        print(f"  {i}) {label}")
    keys = [k for k, _ in options]
    default_idx = keys.index(default) + 1 if default in keys else None
    prompt = f"Scelta [1-{len(options)}]" + (f" (invio = {default_idx})" if default_idx else "") + ": "
    while True:
        answer = input(prompt).strip()
        if not answer and default_idx:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return keys[int(answer) - 1]
        print("Scelta non valida.")


def ask_text(question: str, default: str = "") -> str:
    if not interactive():
        return default
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


def table(headers: list[str], rows: list[list[object]]) -> None:
    cells = [[str(c) for c in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    for n, r in enumerate(cells):
        print("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)))
        if n == 0:
            print("  " + "  ".join("-" * w for w in widths))
