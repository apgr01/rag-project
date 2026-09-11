"""
describe_pages.py

Per ogni pagina già renderizzata in immagine (vedi render_pages.py), invia
l'immagine + il primer di contesto a Gemini e salva la descrizione generata.

Usa un manifest.json per tenere traccia dello stato di avanzamento.
Gestisce automaticamente più API Key caricate nel file .env (GEMINI_API_KEY_01,
GEMINI_API_KEY_02, ecc.) ruotando chiave quando una quota viene superata (429) —
logica condivisa con rag/query.py tramite common/gemini_client.py.

Setup:
    pip install google-genai python-dotenv tqdm
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from google.genai import types
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))  # cosi' troviamo il pacchetto common/ dalla root

from common.gemini_client import KeyManager, QuotaExhausted, generate_with_rotation  # noqa: E402

IMAGES_DIR = SCRIPT_DIR / "../data/images"
DESCRIPTIONS_DIR = SCRIPT_DIR / "../data/descriptions"
PRIMERS_DIR = SCRIPT_DIR / "../config/primers"

DEFAULT_MODEL = "gemini-3.6-flash"
MAX_ATTEMPTS = 5
MAX_CONSECUTIVE_FAILURES = 5
DEFAULT_RPM = 10

PROMPT_TEMPLATE = """Ti fornisco il contesto generale di un catalogo PDF e l'immagine di una sua pagina specifica.

CONTESTO DEL CATALOGO:
{primer}

ISTRUZIONI:
Analizza l'immagine di questa pagina e scrivi una descrizione dettagliata e discorsiva,
pensata per essere usata come contenuto indicizzato in un sistema di ricerca (RAG).
Includi:
- di cosa tratta la pagina (es. quale motore/sezione/argomento)
- il contenuto delle eventuali tabelle, descritto in modo che sia comprensibile senza vedere l'immagine
- cosa rappresentano eventuali immagini/schemi tecnici presenti
- qualunque codice o identificativo visibile, trascritto per quanto possibile fedelmente

Scrivi solo la descrizione, in italiano, senza premesse tipo "Ecco la descrizione:".
"""


def load_primer(primer_name: str) -> str:
    primer_path = PRIMERS_DIR / f"{primer_name}.md"
    if not primer_path.exists():
        raise FileNotFoundError(f"Primer non trovato: {primer_path}")
    return primer_path.read_text(encoding="utf-8")


# --- Gestione manifest ---------------------------------------------------

def manifest_path_for(pdf_stem: str) -> Path:
    return DESCRIPTIONS_DIR / pdf_stem / "manifest.json"


def load_manifest(pdf_stem: str) -> dict:
    path = manifest_path_for(pdf_stem)

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    images_dir = IMAGES_DIR / pdf_stem
    image_files = sorted(images_dir.glob("page-*.png"))

    if not image_files:
        raise FileNotFoundError(
            f"Nessuna immagine trovata in {images_dir}. "
            f"Esegui prima render_pages.py per questo PDF."
        )

    n_pages = len(image_files)

    return {
        "source_pdf": f"{pdf_stem}.pdf",
        "total_pages": n_pages,
        "pages": {
            str(n): {"status": "pending", "attempts": 0}
            for n in range(1, n_pages + 1)
        },
    }


def save_manifest(pdf_stem: str, manifest: dict) -> None:
    path = manifest_path_for(pdf_stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def pages_to_process(manifest: dict, force: bool) -> list[int]:
    if force:
        return sorted(int(n) for n in manifest["pages"].keys())

    eligible = []
    for page_num, info in manifest["pages"].items():
        if info["status"] == "pending":
            eligible.append(int(page_num))
        elif info["status"] == "failed" and info.get("attempts", 0) < MAX_ATTEMPTS:
            eligible.append(int(page_num))
    return sorted(eligible)


# --- Chiamata a Gemini per una singola pagina ---------------------------

def describe_page(key_manager: KeyManager, image_path: Path, primer_text: str, model: str) -> str:
    image_bytes = image_path.read_bytes()
    prompt = PROMPT_TEMPLATE.format(primer=primer_text)

    # generate_with_rotation si occupa gia' di ruotare la chiave e ritentare
    # se scatta un 429: qui non serve piu' gestirlo a mano.
    response = generate_with_rotation(
        key_manager,
        log=tqdm.write,  # cosi' i messaggi di rotazione non rompono la progress bar
        model=model,
        contents=[
            prompt,
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        ],
        config=types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        ),
    )

    if not response.candidates:
        raise ValueError("Risposta senza candidati (probabile blocco di sicurezza).")

    finish_reason = response.candidates[0].finish_reason
    if finish_reason is not None and str(finish_reason) not in ("STOP", "FinishReason.STOP"):
        raise ValueError(f"Generazione interrotta, finish_reason={finish_reason}")

    text = response.text
    if not text or not text.strip():
        raise ValueError("Risposta vuota da Gemini.")

    return text.strip()


# --- Elaborazione di una pagina, con aggiornamento del manifest ---------

def process_page(
    key_manager: KeyManager,
    pdf_stem: str,
    page_num: int,
    primer_text: str,
    primer_name: str,
    model: str,
    manifest: dict,
) -> str:
    """Ritorna 'done' o 'failed'. Solleva QuotaExhausted se TUTTE le chiavi sono finite."""
    image_path = IMAGES_DIR / pdf_stem / f"page-{page_num:04d}.png"
    page_key = str(page_num)
    page_info = manifest["pages"][page_key]

    if not image_path.exists():
        page_info.update({
            "status": "failed",
            "attempts": page_info.get("attempts", 0) + 1,
            "last_error": f"Immagine mancante: {image_path}",
            "last_attempt": datetime.now(timezone.utc).isoformat(),
        })
        return "failed"

    try:
        description = describe_page(key_manager, image_path, primer_text, model)
    except QuotaExhausted:
        raise
    except Exception as e:
        page_info.update({
            "status": "failed",
            "attempts": page_info.get("attempts", 0) + 1,
            "last_error": str(e),
            "last_attempt": datetime.now(timezone.utc).isoformat(),
        })
        return "failed"

    out_path = DESCRIPTIONS_DIR / pdf_stem / f"page-{page_num:04d}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "source_pdf": manifest["source_pdf"],
            "page_number": page_num,
            "primer_version": primer_name,
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "description": description,
        }, f, indent=2, ensure_ascii=False)

    page_info.update({
        "status": "done",
        "attempts": page_info.get("attempts", 0) + 1,
        "last_error": None,
        "last_attempt": datetime.now(timezone.utc).isoformat(),
    })
    return "done"


# --- CLI e ciclo principale ----------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Genera descrizioni per pagine PDF via Gemini.")
    parser.add_argument("--pdf", required=True, help="Nome del PDF senza estensione (es. 'catalogo_kubota').")
    parser.add_argument("--primer", required=True, help="Nome del file primer in config/primers/, senza estensione.")
    parser.add_argument("--force", action="store_true", help="Rigenera tutte le pagine, incluse quelle gia' 'done'.")
    parser.add_argument("--limit", type=int, default=None, help="Elabora al massimo N pagine in questa run.")
    parser.add_argument("--rpm", type=int, default=DEFAULT_RPM, help=f"Richieste al minuto massime (default: {DEFAULT_RPM}).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Modello Gemini da usare (default: {DEFAULT_MODEL}).")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        key_manager = KeyManager()
        primer_text = load_primer(args.primer)
        manifest = load_manifest(args.pdf)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"❌ {e}")
        sys.exit(1)

    todo = pages_to_process(manifest, args.force)
    if args.limit is not None:
        todo = todo[:args.limit]

    if not todo:
        print("✅ Nessuna pagina da elaborare (tutto gia' fatto — usa --force per rigenerare).")
        return

    print(f"🔑 Caricate {len(key_manager.keys)} API Key. Chiave iniziale: {key_manager.current_key_name}")
    print(f"🚀 In elaborazione {len(todo)} pagine di '{args.pdf}' con il modello '{args.model}'.")

    delay_between_calls = 60.0 / args.rpm
    consecutive_failures = 0
    done_count = 0
    failed_count = 0

    try:
        for page_num in tqdm(todo, desc=args.pdf, unit="pagina"):
            try:
                result = process_page(
                    key_manager, args.pdf, page_num, primer_text, args.primer, args.model, manifest
                )
            except QuotaExhausted as e:
                print(f"\n⚠️  {e}")
                print("Interrompo la run. Le pagine rimanenti restano 'pending' e "
                      "verranno riprese al prossimo avvio (magari domani, a quota rinnovata).")
                save_manifest(args.pdf, manifest)
                sys.exit(0)

            if result == "done":
                done_count += 1
                consecutive_failures = 0
            else:
                failed_count += 1
                consecutive_failures += 1

            save_manifest(args.pdf, manifest)

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n⚠️  {consecutive_failures} fallimenti consecutivi (non di quota). "
                      f"Interrompo per controllo manuale.")
                break

            time.sleep(delay_between_calls)

    except KeyboardInterrupt:
        print("\n⏸️  Interrotto manualmente. Salvo lo stato...")
        save_manifest(args.pdf, manifest)
        sys.exit(0)

    save_manifest(args.pdf, manifest)
    print(f"\nFatto. ✅ {done_count} completate, ❌ {failed_count} fallite in questa run.")
    if failed_count:
        print("Rilancia lo script senza --force per ritentare automaticamente le pagine fallite.")


if __name__ == "__main__":
    main()