import argparse
import pymupdf
from pathlib import Path
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).resolve().parent
PDFS_DIR = SCRIPT_DIR / "../data/pdfs"
IMAGES_DIR = SCRIPT_DIR / "../data/images"

DEFAULT_DPI = 150


def parse_args():
    parser = argparse.ArgumentParser(description="Renderizza i PDF in immagini pagina.")
    parser.add_argument("--force", action="store_true", help="Rigenera anche se un rendering esiste già.")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help=f"Risoluzione di rendering (default: {DEFAULT_DPI}).")
    return parser.parse_args()


def render_pdf(doc, output_dir: Path, dpi: int, pdf_name: str) -> int:
    for i, page in enumerate(tqdm(doc, desc=pdf_name, unit="pagina")):
        pix = page.get_pixmap(dpi=dpi)
        pix.save(output_dir / f"page-{i+1:04d}.png")
    return len(doc)


def process_pdf(pdf_path: Path, force: bool, dpi: int) -> None:
    output_dir = IMAGES_DIR / pdf_path.stem

    try:
        with pymupdf.open(pdf_path) as doc:
            n_pages = len(doc)
            existing_images = list(output_dir.glob("page-*.png")) if output_dir.exists() else []
            already_done = len(existing_images) == n_pages

            if already_done and not force:
                print(f"⏭️  {pdf_path.name}: già renderizzato ({n_pages} pagine), salto.")
                return

            if force and output_dir.exists():
                for old_file in output_dir.glob("page-*.png"):
                    old_file.unlink()
                print(f"🗑️  {pdf_path.name}: rendering precedente rimosso (--force).")

            output_dir.mkdir(parents=True, exist_ok=True)
            render_pdf(doc, output_dir, dpi, pdf_path.name)
            print(f"✅ {pdf_path.name}: renderizzate {n_pages} pagine.")

    except Exception as e:
        print(f"❌ {pdf_path.name}: errore durante l'elaborazione — {e}")


def main():
    args = parse_args()

    pdf_files = list(PDFS_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"⚠️ Nessun file PDF trovato nella cartella: {PDFS_DIR.resolve()}")
        return

    for pdf_path in pdf_files:
        process_pdf(pdf_path, force=args.force, dpi=args.dpi)


if __name__ == "__main__":
    main()