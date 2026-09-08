"""
describe_pages.py

Per ogni pagina già renderizzata in immagine (vedi render_pages.py), invia
l'immagine + il primer di contesto a Gemini e salva la descrizione generata.

Usa un manifest.json per tenere traccia dello stato di avanzamento: puoi
interrompere lo script in qualsiasi momento e rilanciarlo, riparte da dove
aveva lasciato senza ripetere chiamate già andate a buon fine.

SICUREZZA SUI COSTI:
Questo script è pensato per il tier GRATUITO di Google AI Studio (API key
senza account di fatturazione collegato). In quello stato, superare la
quota produce solo errori 429 "RESOURCE_EXHAUSTED" — non un addebito.
Non collegare un account di fatturazione al progetto Google se vuoi
mantenere questa garanzia.

Setup:
    pip install google-genai python-dotenv tqdm
    # nel file .env nella root del progetto:
    # GEMINI_API_KEY=la_tua_chiave

Uso:
    python describe_pages.py --pdf catalogo_kubota --primer kubota_catalog_v1
    python describe_pages.py --pdf catalogo_kubota --primer kubota_catalog_v1 --limit 5   # test su poche pagine
    python describe_pages.py --pdf catalogo_kubota --primer kubota_catalog_v1 --force     # rigenera tutto
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from tqdm import tqdm

# --- Configurazione ---------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
IMAGES_DIR = SCRIPT_DIR / "../data/images"
DESCRIPTIONS_DIR = SCRIPT_DIR / "../data/descriptions"
PRIMERS_DIR = SCRIPT_DIR / "../config/primers"

# Verifica sempre su https://ai.google.dev/gemini-api/docs/models se questo
# e' ancora il modello Flash stabile corrente: i modelli vengono ritirati
# regolarmente (Gemini 2.5 Flash, ad esempio, va in pensione il 16/10/2026).
DEFAULT_MODEL = "gemini-3.6-flash"

# Quante volte ritentare una pagina che fallisce per un errore "non di quota"
# (es. immagine illeggibile, risposta vuota) prima di arrendersi su quella
# pagina specifica e passare oltre.
MAX_ATTEMPTS = 5

# Dopo quanti fallimenti CONSECUTIVI (non di quota) interrompere l'intera run.
# Protegge da scenari come "il primer e' rotto e ogni pagina fallisce allo
# stesso modo" — meglio fermarsi e controllare che continuare a bruciare
# quota giornaliera su richieste destinate a fallire comunque.
MAX_CONSECUTIVE_FAILURES = 5

# Richieste al minuto: tienilo prudente e sotto il limite del tier gratuito
# per il modello che usi (controlla il limite RPM attuale sulla pagina dei
# rate limit di Google). Un valore prudente lascia margine ad altre
# eventuali chiamate che fai nello stesso periodo (es. test manuali).
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


# --- Setup client e primer ---------------------------------------------

def get_client() -> genai.Client:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY non trovata. Controlla che il file .env esista "
            "nella root del progetto e contenga GEMINI_API_KEY=la_tua_chiave."
        )
    return genai.Client(api_key=api_key)


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

class QuotaExhausted(Exception):
    """Segnala che la quota (giornaliera, quasi certamente) e' esaurita:
    non ha senso continuare a tentare altre pagine in questa run."""
    pass


def describe_page(client: genai.Client, image_path: Path, primer_text: str, model: str) -> str:
    image_bytes = image_path.read_bytes()
    prompt = PROMPT_TEMPLATE.format(primer=primer_text)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            ],
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            ),
        )
    except genai_errors.ClientError as e:
        if e.code == 429:
            # Il SDK ritenta gia' da solo gli errori transitori (fino a 4 volte
            # con backoff): se l'errore arriva comunque fin qui, molto
            # probabilmente e' la quota GIORNALIERA esaurita, non un picco
            # temporaneo. Non ha senso continuare a martellare l'API.
            raise QuotaExhausted(str(e)) from e
        # Altri errori 4xx (es. 400 immagine non valida, 403 permessi):
        # non sono recuperabili ritentando la stessa richiesta identica.
        raise

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
    client: genai.Client,
    pdf_stem: str,
    page_num: int,
    primer_text: str,
    primer_name: str,
    model: str,
    manifest: dict,
) -> str:
    """Ritorna 'done', 'failed', o solleva QuotaExhausted."""
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
        description = describe_page(client, image_path, primer_text, model)
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
    parser.add_argument("--limit", type=int, default=None, help="Elabora al massimo N pagine in questa run (utile per test).")
    parser.add_argument("--rpm", type=int, default=DEFAULT_RPM, help=f"Richieste al minuto massime (default: {DEFAULT_RPM}).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Modello Gemini da usare (default: {DEFAULT_MODEL}).")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        client = get_client()
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

    print(f"In elaborazione {len(todo)} pagine di '{args.pdf}' con il modello '{args.model}'.")

    delay_between_calls = 60.0 / args.rpm
    consecutive_failures = 0
    done_count = 0
    failed_count = 0

    try:
        for page_num in tqdm(todo, desc=args.pdf, unit="pagina"):
            try:
                result = process_page(
                    client, args.pdf, page_num, primer_text, args.primer, args.model, manifest
                )
            except QuotaExhausted as e:
                print(f"\n⚠️  Quota esaurita alla pagina {page_num}: {e}")
                print("Interrompo qui. Le pagine non ancora fatte restano 'pending' "
                      "e verranno riprese al prossimo avvio dello script.")
                save_manifest(args.pdf, manifest)
                sys.exit(0)

            if result == "done":
                done_count += 1
                consecutive_failures = 0
            else:
                failed_count += 1
                consecutive_failures += 1

            # Salvo il manifest dopo OGNI pagina, non solo alla fine:
            # se lo script viene interrotto (Ctrl+C, crash, chiusura del PC),
            # il progresso fatto finora non va perso.
            save_manifest(args.pdf, manifest)

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n⚠️  {consecutive_failures} fallimenti consecutivi (non di quota). "
                      f"Interrompo per controllo manuale — probabile problema sistematico "
                      f"(primer, formato immagini, ecc.), non ha senso continuare a consumare quota.")
                break

            time.sleep(delay_between_calls)

    except KeyboardInterrupt:
        print("\n⏸️  Interrotto manualmente. Salvo lo stato...")
        save_manifest(args.pdf, manifest)
        sys.exit(0)

    save_manifest(args.pdf, manifest)
    print(f"\nFatto. ✅ {done_count} completate, ❌ {failed_count} fallite in questa run.")
    if failed_count:
        print("Rilancia lo script senza --force per ritentare automaticamente le pagine fallite "
              "(fino al limite di tentativi configurato).")


if __name__ == "__main__":
    main()