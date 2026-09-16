import json
import time
import pymupdf
import threading
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime, timezone
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from ingestion.base import BaseIngestor
from common.config import Config
from common.gemini_client import GeminiClient
from common.vector_store import VectorStore
from common.paths import IMAGES_DIR, DESCRIPTIONS_DIR, PROJECT_ROOT

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

MAX_ATTEMPTS = 5
MAX_CONSECUTIVE_FAILURES = 5


class StandardIngestor(BaseIngestor):
    def __init__(self, config: Config, gemini_client: GeminiClient, vector_store: VectorStore):
        self.config = config
        self.gemini_client = gemini_client
        self.vector_store = vector_store
        
        self.images_dir = IMAGES_DIR
        self.descriptions_dir = DESCRIPTIONS_DIR

        # Lock per rendere thread-safe l'accesso al file manifest e ai contatori globali
        self._manifest_lock = threading.Lock()
        self._failures_lock = threading.Lock()
        self.consecutive_failures = 0

    def process_document(self, file_path: str, force: bool = False, rpm: int = 10) -> Dict[str, Any]:
        pdf_path = Path(file_path)
        if not pdf_path.is_absolute():
            pdf_path = PROJECT_ROOT / pdf_path

        if not pdf_path.exists():
            raise FileNotFoundError(f"File PDF non trovato: {pdf_path}")

        pdf_stem = pdf_path.stem
        print(f"🚀 Avvio Ingestion Vision per: {pdf_path.name}")

        img_paths = self._render_pdf_to_images(pdf_path, force=force)
        manifest = self._load_or_create_manifest(pdf_stem, len(img_paths))
        primer_text = self._load_primer()

        descriptions = self._process_pages_with_manifest(
            pdf_stem=pdf_stem,
            img_paths=img_paths,
            primer_text=primer_text,
            manifest=manifest,
            force=force,
            rpm=rpm
        )

        chunks_count = self._index_descriptions_to_chroma(pdf_stem, descriptions)

        stats = {
            "status": "success",
            "pdf_name": pdf_path.name,
            "total_pages": len(img_paths),
            "indexed_chunks": chunks_count
        }
        print(f"✅ Ingestion completata per '{pdf_path.name}': {chunks_count} pagine sincronizzate su ChromaDB.")
        return stats

    def _render_pdf_to_images(self, pdf_path: Path, force: bool) -> List[Path]:
        output_dir = self.images_dir / pdf_path.stem

        if force and output_dir.exists():
            for old_file in output_dir.glob("page-*.png"):
                old_file.unlink()

        output_dir.mkdir(parents=True, exist_ok=True)
        dpi = self.config.ingestion.render_dpi

        with pymupdf.open(pdf_path) as doc:
            existing = list(output_dir.glob("page-*.png"))
            if len(existing) == len(doc) and not force:
                return sorted(list(output_dir.glob("page-*.png")))

            for i, page in enumerate(tqdm(doc, desc=f"Rendering {pdf_path.name}", unit="pag")):
                img_path = output_dir / f"page-{i+1:04d}.png"
                pix = page.get_pixmap(dpi=dpi)
                pix.save(img_path)

        return sorted(list(output_dir.glob("page-*.png")))

    def _load_or_create_manifest(self, pdf_stem: str, total_pages: int) -> dict:
        manifest_path = self.descriptions_dir / pdf_stem / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)

        return {
            "source_pdf": f"{pdf_stem}.pdf",
            "total_pages": total_pages,
            "pages": {
                str(n): {"status": "pending", "attempts": 0}
                for n in range(1, total_pages + 1)
            }
        }

    def _save_manifest(self, pdf_stem: str, manifest: dict) -> None:
        manifest_path = self.descriptions_dir / pdf_stem / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def _load_primer(self) -> str:
        primer_path = Path(self.config.ingestion.primer_path)
        if not primer_path.is_absolute():
            primer_path = PROJECT_ROOT / primer_path
            
        if not primer_path.exists():
            raise FileNotFoundError(f"Primer non trovato: {primer_path}")

        return primer_path.read_text(encoding="utf-8")

    def _process_pages_with_manifest(self, pdf_stem: str, img_paths: List[Path], primer_text: str, manifest: dict, force: bool, rpm: int) -> List[Dict[str, Any]]:
        desc_out_dir = self.descriptions_dir / pdf_stem
        desc_out_dir.mkdir(parents=True, exist_ok=True)
        prompt = PROMPT_TEMPLATE.format(primer=primer_text)

        todo = []
        for page_num_str, info in manifest["pages"].items():
            p_num = int(page_num_str)
            if force or info["status"] == "pending":
                todo.append(p_num)
            elif info["status"] == "failed" and info.get("attempts", 0) < MAX_ATTEMPTS:
                todo.append(p_num)

        todo.sort()
        if not todo:
            return self._collect_existing_descriptions(pdf_stem)

        delay_between_calls = 60.0 / rpm
        self.consecutive_failures = 0

        # Calcola il numero di worker in base alle API keys disponibili (minimo 1)
        num_workers = max(1, len(getattr(self.gemini_client, 'api_keys', [1])))

        def _worker(page_num):
            # Se abbiamo superato i fallimenti, abortiamo rapidamente il thread
            with self._failures_lock:
                if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    return False, page_num, "Interrotto per troppi fallimenti consecutivi nel sistema"

            page_key = str(page_num)
            img_path = self.images_dir / pdf_stem / f"page-{page_num:04d}.png"
            json_out_path = desc_out_dir / f"page-{page_num:04d}.json"

            try:
                description_text = self.gemini_client.generate_with_vision(
                    prompt=prompt,
                    image_path=str(img_path),
                    model_name=self.config.rag.flash_model
                )

                desc_data = {
                    "source_pdf": manifest["source_pdf"],
                    "page_number": page_num,
                    "model": self.config.rag.flash_model,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "description": description_text.strip()
                }

                with open(json_out_path, "w", encoding="utf-8") as f:
                    json.dump(desc_data, f, indent=2, ensure_ascii=False)

                with self._manifest_lock:
                    manifest["pages"][page_key].update({
                        "status": "done",
                        "attempts": manifest["pages"][page_key].get("attempts", 0) + 1,
                        "last_error": None,
                        "last_attempt": datetime.now(timezone.utc).isoformat()
                    })
                    self._save_manifest(pdf_stem, manifest)

                with self._failures_lock:
                    self.consecutive_failures = 0

                time.sleep(delay_between_calls)
                return True, page_num, None

            except Exception as e:
                with self._manifest_lock:
                    manifest["pages"][page_key].update({
                        "status": "failed",
                        "attempts": manifest["pages"][page_key].get("attempts", 0) + 1,
                        "last_error": str(e),
                        "last_attempt": datetime.now(timezone.utc).isoformat()
                    })
                    self._save_manifest(pdf_stem, manifest)

                with self._failures_lock:
                    self.consecutive_failures += 1

                time.sleep(delay_between_calls)
                return False, page_num, str(e)

        print(f"⚙️ Esecuzione in parallelo con {num_workers} worker (API keys)...")
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Sottomettiamo tutti i task all'executor
            future_to_page = {executor.submit(_worker, p): p for p in todo}
            
            # tqdm viene aggiornato non appena un thread finisce il suo lavoro
            with tqdm(total=len(todo), desc=f"Descrizione {pdf_stem}", unit="pag") as pbar:
                for future in as_completed(future_to_page):
                    success, page_num, err_msg = future.result()
                    if not success and "Interrotto per troppi fallimenti" not in err_msg:
                        tqdm.write(f"⚠️ Errore alla pagina {page_num}: {err_msg}")
                    pbar.update(1)

        with self._failures_lock:
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n❌ Interruzione generale: {MAX_CONSECUTIVE_FAILURES} fallimenti consecutivi raggiunti.")

        return self._collect_existing_descriptions(pdf_stem)

    def _collect_existing_descriptions(self, pdf_stem: str) -> List[Dict[str, Any]]:
        desc_out_dir = self.descriptions_dir / pdf_stem
        results = []
        for json_file in sorted(desc_out_dir.glob("page-*.json")):
            with open(json_file, "r", encoding="utf-8") as f:
                results.append(json.load(f))
        return results

    def _index_descriptions_to_chroma(self, pdf_stem: str, descriptions: List[Dict[str, Any]]) -> int:
        ids, documents, metadatas = [], [], []

        for desc in descriptions:
            page_num = desc["page_number"]
            text = desc["description"]
            if not text:
                continue

            chunk_id = f"{pdf_stem}_page_{page_num}"
            ids.append(chunk_id)
            documents.append(text)
            metadatas.append({
                "source": desc["source_pdf"],
                "page": page_num,
                "mode": "standard_vision"
            })

        if documents:
            self.vector_store.add_documents(ids=ids, documents=documents, metadatas=metadatas)

        return len(documents)