"""Sessione guidata di configurazione: primer e specifica costruiti insieme a Gemini.

L'utente non deve leggersi tutto il catalogo né scrivere a mano cosa indicizzare:

1. sceglie alcune **pagine significative** (indice, legende, pagine tipo) che vengono
   inviate a Gemini come estratto PDF, con la corrispondenza delle pagine nel prompt;
2. dice, se vuole, **cosa vuole ottenere** (o lascia proporre a Gemini) e può indicare
   un **CSV di riferimento** (esempio di formato e metro di confronto);
3. Gemini scrive **primer e specifica** e propone alcune **pagine di prova**;
4. il programma esegue un'**anteprima** dell'estrazione su quelle pagine, con le stesse
   identiche regole dell'indicizzazione vera, e la confronta con il riferimento;
5. l'utente scrive cosa non va **a parole**; Gemini aggiorna primer e specifica tenendo
   conto delle osservazioni precedenti e dei risultati della prova; si ripete;
6. alla fine primer e specifica vengono **salvati** nel workspace.

Ogni versione ("giro") resta su disco in ``documents/<id>/setup/rounds/NN/``: si può
tornare indietro, interrompere e riprendere. Questo modulo contiene solo la logica;
l'interazione con l'utente è in ``cli_setup.py``.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ragpdf.app import App
from ragpdf.core.documents import Document
from ragpdf.core.fileio import atomic_write_text, read_json, utc_now, write_json
from ragpdf.core.page_runner import RunControl, RunReport
from ragpdf.core.paths import EXAMPLES_DIR
from ragpdf.core.pdf_tools import build_subset_pdf, page_mapping_text
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import parse_page_ranges
from ragpdf.indexing.assemble import AssembledIndex, assemble
from ragpdf.indexing.compare import Comparison, compare_rows, filter_reference
from ragpdf.indexing.export import write_index
from ragpdf.indexing.spec import IndexingSpec, SpecError, load_spec, read_csv_rows
from ragpdf.primer import MAX_SELECTED_PAGES, clean_markdown, sample_pages

MAX_PREVIEW_PAGES = 6
MAX_PREVIEW_ROWS_IN_PROMPT = 60
MAX_ANOMALIES_IN_PROMPT = 25

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primer_markdown": {"type": "string"},
        "spec_yaml": {"type": "string"},
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
        "preview_pages": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["primer_markdown", "spec_yaml", "summary", "questions", "preview_pages"],
}


class SetupError(Exception):
    """Errore della sessione guidata, con messaggio per l'utente."""


@dataclass
class SetupState:
    doc_id: str
    primer_name: str
    spec_name: str
    context_pages: list[int]
    preview_pages: list[int] = field(default_factory=list)
    goal: str = ""
    reference_csv: str | None = None
    current_round: int = 0
    last_round: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    saved_at: str | None = None


@dataclass
class Preview:
    report: RunReport
    index: AssembledIndex
    csv_path: Path
    anomalies_path: Path | None
    comparison: Comparison | None = None
    comparison_note: str = ""
    rows_per_page: dict[int, int] = field(default_factory=dict)


def default_context_pages(page_count: int) -> list[int]:
    """Proposta di pagine da mostrare a Gemini se l'utente non ne indica."""
    return sample_pages(page_count, first=6, total=14)


class SetupSession:
    def __init__(self, app: App, doc: Document, state: SetupState):
        self.app = app
        self.doc = doc
        self.state = state

    # ------------------------------------------------------------------ percorsi
    @staticmethod
    def folder_for(doc: Document) -> Path:
        return doc.folder / "setup"

    @property
    def folder(self) -> Path:
        return self.folder_for(self.doc)

    def round_dir(self, n: int | None = None) -> Path:
        return self.folder / "rounds" / f"{(n if n is not None else self.state.current_round):02d}"

    @property
    def primer_path(self) -> Path:
        return self.round_dir() / "primer.md"

    @property
    def spec_path(self) -> Path:
        return self.round_dir() / "spec.yaml"

    @property
    def preview_csv_path(self) -> Path:
        return self.round_dir() / "anteprima.csv"

    # ------------------------------------------------------------- persistenza
    @classmethod
    def load(cls, app: App, doc: Document) -> SetupSession | None:
        path = cls.folder_for(doc) / "session.json"
        if not path.exists():
            return None
        return cls(app, doc, SetupState(**read_json(path)))

    def save_state(self) -> None:
        write_json(self.folder / "session.json", asdict(self.state))

    @classmethod
    def new(
        cls,
        app: App,
        doc: Document,
        context_pages: list[int],
        goal: str = "",
        reference_csv: Path | None = None,
        primer_name: str | None = None,
        spec_name: str | None = None,
    ) -> SetupSession:
        if not context_pages:
            raise SetupError("Indica almeno una pagina da mostrare a Gemini.")
        if len(context_pages) > MAX_SELECTED_PAGES:
            raise SetupError(
                f"Hai indicato {len(context_pages)} pagine: il massimo è {MAX_SELECTED_PAGES}. "
                "Scegli le più rappresentative (indice, legende, alcune pagine tipo)."
            )
        if reference_csv is not None and not Path(reference_csv).is_file():
            raise SetupError(f"CSV di riferimento non trovato: {reference_csv}")
        folder = cls.folder_for(doc)
        if folder.exists():
            shutil.rmtree(folder)  # una nuova sessione sostituisce quella vecchia
        state = SetupState(
            doc_id=doc.id,
            primer_name=primer_name or doc.id,
            spec_name=spec_name or doc.id,
            context_pages=context_pages,
            goal=goal.strip(),
            reference_csv=str(Path(reference_csv).resolve()) if reference_csv else None,
        )
        session = cls(app, doc, state)
        session.save_state()
        return session

    # ----------------------------------------------------------------- lettura
    def primer_text(self) -> str:
        return self.primer_path.read_text(encoding="utf-8") if self.primer_path.exists() else ""

    def spec_text(self) -> str:
        return self.spec_path.read_text(encoding="utf-8") if self.spec_path.exists() else ""

    def load_spec(self) -> IndexingSpec:
        return load_spec(self.spec_path)

    def round_notes(self, n: int | None = None) -> dict[str, Any]:
        path = self.round_dir(n) / "round.json"
        return read_json(path) if path.exists() else {}

    # --------------------------------------------------------- costruzione prompt
    def _example_block(self) -> str:
        if not self.state.reference_csv:
            return ""
        lines = Path(self.state.reference_csv).read_text(encoding="utf-8-sig", errors="replace").splitlines()
        return (
            "\n## CSV D'ESEMPIO FORNITO DALL'UTENTE (prime righe di un indice già fatto)\n"
            "Riproduci nomi, ordine delle colonne e stile dei valori.\n```csv\n" + "\n".join(lines[:21]) + "\n```\n"
        )

    def _pages_note(self) -> str:
        return page_mapping_text(self.state.context_pages, self.doc.info.page_count) + (
            "\nIndica sempre i numeri di pagina del documento ORIGINALE."
        )

    def _goal_text(self) -> str:
        return self.state.goal or (
            "L'utente non ha indicato cosa vuole ottenere: proponi tu l'indicizzazione più utile "
            "per questo documento (ad esempio, per un catalogo di ricambi, una riga per ogni codice "
            "articolo con i dati che permettono di cercarlo)."
        )

    def _preview_block(self) -> str:
        notes = self.round_notes()
        preview = notes.get("preview")
        if not preview:
            return "(nessuna prova eseguita)"
        parts = [f"Pagine provate: {', '.join(map(str, preview['pages']))}."]
        per_page = ", ".join(f"pagina {p}: {n} righe" for p, n in preview["rows_per_page"].items())
        parts.append(f"Righe estratte per pagina: {per_page}.")
        if preview.get("failed"):
            parts.append(f"Pagine fallite: {preview['failed']}")
        if self.preview_csv_path.exists():
            lines = self.preview_csv_path.read_text(encoding="utf-8-sig").splitlines()
            shown = lines[: MAX_PREVIEW_ROWS_IN_PROMPT + 1]
            more = len(lines) - len(shown)
            label = f" (prime {MAX_PREVIEW_ROWS_IN_PROMPT} righe, altre {more} omesse)" if more > 0 else ""
            parts.append(f"CSV prodotto{label}:\n```csv\n" + "\n".join(shown) + "\n```")
        if preview.get("anomalies"):
            parts.append("Anomalie rilevate dai controlli:\n" + "\n".join(f"- {a}" for a in preview["anomalies"]))
        if preview.get("comparison"):
            c = preview["comparison"]
            parts.append(
                f"Confronto con il CSV di riferimento dell'utente sulle stesse pagine: copertura {c['recall']:.0%}, "
                f"precisione {c['precision']:.0%}."
            )
            if c.get("missing"):
                parts.append("Righe del riferimento NON trovate:\n" + "\n".join(f"- {r}" for r in c["missing"]))
            if c.get("extra"):
                parts.append("Righe prodotte NON presenti nel riferimento:\n" + "\n".join(f"- {r}" for r in c["extra"]))
        return "\n".join(parts)

    def _history_block(self) -> str:
        items = [h for h in self.state.history if h.get("feedback")]
        if not items:
            return "(nessuna)"
        return "\n".join(f"- {h['feedback']}" for h in items)

    # ----------------------------------------------------------- chiamate Gemini
    def _ask_gemini(self, prompt: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            subset = build_subset_pdf(self.doc.pdf_path, self.state.context_pages, Path(tmp) / "estratto.pdf")
            response = self.app.client().generate_json(prompt, RESPONSE_SCHEMA, pdf=subset)
        if not isinstance(response, dict) or not response.get("spec_yaml") or not response.get("primer_markdown"):
            raise SetupError("Gemini ha restituito una risposta incompleta. Riprova.")
        return response

    def _store_round(self, response: dict[str, Any], feedback: str, origin: str) -> int:
        """Valida la risposta, la salva come nuovo giro e lo rende corrente."""
        n = self.state.last_round + 1
        folder = self.round_dir(n)
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / "primer.md", clean_markdown(response["primer_markdown"]))
        self._write_spec(folder / "spec.yaml", response["spec_yaml"], response.get("_base_dir"))
        write_json(
            folder / "round.json",
            {
                "round": n,
                "origin": origin,
                "feedback": feedback,
                "summary": str(response.get("summary", "")).strip(),
                "questions": [str(q) for q in response.get("questions") or []],
                "suggested_preview_pages": self._valid_preview_pages(
                    response.get("preview_pages"), from_gemini=origin in ("iniziale", "revisione")
                ),
                "created_at": utc_now(),
            },
        )
        self.state.last_round = n
        self.state.current_round = n
        self.state.history.append({"round": n, "feedback": feedback, "origin": origin})
        suggested = self.round_notes(n)["suggested_preview_pages"]
        if suggested:
            self.state.preview_pages = suggested
        elif not self.state.preview_pages:
            self.state.preview_pages = self._fallback_preview_pages()
        self.save_state()
        return n

    def _write_spec(self, path: Path, spec_yaml: str, base_dir: Path | None = None) -> None:
        """Scrive la specifica forzando nome ed esempio, poi la valida (SpecError se non valida).

        ``base_dir``: cartella da cui proviene la specifica (per i percorsi relativi del CSV
        d'esempio). Un CSV d'esempio che non esiste viene tolto: Gemini a volte copia dal
        modello un nome di file inventato.
        """
        text = spec_yaml.strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            atomic_write_text(path, text + "\n")
            raise SpecError(f"YAML non valido: {e}") from e
        if not isinstance(data, dict):
            atomic_write_text(path, text + "\n")
            raise SpecError("La specifica non è un insieme di campi YAML.")
        data["name"] = self.state.spec_name

        examples = data.get("examples") if isinstance(data.get("examples"), dict) else {}
        if self.state.reference_csv:
            examples["csv"] = self.state.reference_csv
        elif examples.get("csv"):
            examples["csv"] = self._resolve_example(str(examples["csv"]), base_dir)
        if examples:
            data["examples"] = examples

        body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
        atomic_write_text(path, body)
        spec = load_spec(path)
        if spec.pages:
            try:
                parse_page_ranges(spec.pages, self.doc.info.page_count)
            except ValueError as e:
                raise SpecError(f"pages: {e}") from e

    def _resolve_example(self, csv: str, base_dir: Path | None) -> str | None:
        candidate = Path(csv).expanduser()
        options = [candidate] if candidate.is_absolute() else [
            d / candidate for d in (base_dir, self.app.workspace.specs_dir) if d is not None
        ]
        for option in options:
            if option.is_file():
                return str(option.resolve())
        return None

    def _generate(self, prompt: str, feedback: str, origin: str) -> int:
        """Chiama Gemini; se la specifica non è valida chiede una correzione (una volta)."""
        response = self._ask_gemini(prompt)
        try:
            return self._store_round(response, feedback, origin)
        except SpecError as error:
            self._discard_partial_round()
            retry = (
                prompt
                + "\n\n## ATTENZIONE: LA SPECIFICA CHE HAI RESTITUITO NON ERA VALIDA\n"
                + f"Errori:\n{error}\n\nRestituisci di nuovo tutti i campi, con la specifica corretta."
            )
            response = self._ask_gemini(retry)
            try:
                return self._store_round(response, feedback, origin)
            except SpecError as error2:
                self._discard_partial_round()
                raise SetupError(
                    f"Gemini ha prodotto due volte una specifica non valida:\n{error2}\n"
                    "La versione precedente resta quella attuale. Riprova riformulando la richiesta."
                ) from error2

    def _discard_partial_round(self) -> None:
        partial = self.round_dir(self.state.last_round + 1)
        if partial.exists():
            shutil.rmtree(partial)

    def build_initial_prompt(self) -> str:
        """Il prompt del primo giro, esattamente come verrebbe inviato (senza inviarlo)."""
        template = (EXAMPLES_DIR / "specs" / "base.yaml").read_text(encoding="utf-8")
        return render_prompt(
            load_prompt("setup_initial"),
            pages_note=self._pages_note(),
            goal=self._goal_text(),
            example=self._example_block(),
            spec_template=template.strip(),
        )

    def start(self) -> int:
        """Primo giro: Gemini analizza le pagine scelte e scrive primer e specifica."""
        return self._generate(self.build_initial_prompt(), feedback="", origin="iniziale")

    def start_from_existing(self, primer_name: str, spec_name: str) -> int:
        """Primo giro a partire da primer e specifica già esistenti (catalogo dello stesso tipo)."""
        ws = self.app.workspace
        primer = ws.read_primer(primer_name)
        spec_file = ws.spec_path(spec_name)
        if not spec_file.exists():
            raise SetupError(f"Specifica '{spec_name}' non trovata.")
        response = {
            "primer_markdown": primer,
            "spec_yaml": spec_file.read_text(encoding="utf-8"),
            "summary": f"Partenza da primer '{primer_name}' e specifica '{spec_name}' esistenti.",
            "_base_dir": spec_file.parent,
            "questions": [],
            "preview_pages": [],
        }
        try:
            return self._store_round(response, feedback="", origin=f"da {primer_name}/{spec_name}")
        except SpecError as e:
            self._discard_partial_round()
            raise SetupError(f"La specifica '{spec_name}' non è valida:\n{e}") from e

    def build_revise_prompt(self, feedback: str) -> str:
        """Il prompt di revisione, esattamente come verrebbe inviato (senza inviarlo)."""
        return render_prompt(
            load_prompt("setup_revise"),
            pages_note=self._pages_note(),
            goal=self._goal_text(),
            example=self._example_block(),
            primer=self.primer_text().strip(),
            spec=self.spec_text().strip(),
            preview=self._preview_block(),
            history=self._history_block(),
            feedback=feedback.strip(),
        )

    def revise(self, feedback: str) -> int:
        """Nuovo giro: Gemini applica le osservazioni dell'utente."""
        if not feedback.strip():
            raise SetupError("Scrivi cosa vuoi cambiare.")
        return self._generate(self.build_revise_prompt(feedback), feedback=feedback.strip(), origin="revisione")

    def manual_edit_done(self, primer_text: str | None = None, spec_text: str | None = None) -> int:
        """Registra una modifica fatta a mano come nuovo giro (dopo averla validata)."""
        response = {
            "primer_markdown": primer_text if primer_text is not None else self.primer_text(),
            "spec_yaml": spec_text if spec_text is not None else self.spec_text(),
            "summary": "Modifica manuale.",
            "_base_dir": self.round_dir(),
            "questions": [],
            "preview_pages": self.state.preview_pages,
        }
        try:
            return self._store_round(response, feedback="(modifica manuale)", origin="manuale")
        except SpecError:
            self._discard_partial_round()
            raise

    def rounds(self) -> list[dict[str, Any]]:
        """Elenco delle versioni salvate su disco, dalla prima all'ultima."""
        result = []
        for n in range(1, self.state.last_round + 1):
            notes = self.round_notes(n)
            if notes:
                result.append({k: notes.get(k) for k in ("round", "origin", "feedback", "summary", "created_at")})
        return result

    def go_to(self, n: int) -> int:
        """Rende corrente una versione qualsiasi (le altre restano su disco)."""
        if not (self.round_dir(n) / "round.json").exists():
            raise SetupError(f"La versione {n} non esiste.")
        self.state.current_round = n
        self.state.history.append({"round": n, "feedback": "", "origin": "ripristino"})
        self.save_state()
        return n

    def go_back(self) -> int:
        """Torna alla versione precedente (la successiva resta su disco)."""
        previous = [h["round"] for h in self.state.history if h["round"] < self.state.current_round]
        if not previous:
            raise SetupError("Non ci sono versioni precedenti.")
        self.state.current_round = previous[-1]
        self.state.history.append({"round": self.state.current_round, "feedback": "", "origin": "indietro"})
        self.save_state()
        return self.state.current_round

    # ---------------------------------------------------------------- pagine
    def _valid_preview_pages(self, pages: Any, from_gemini: bool = False) -> list[int]:
        numbers = []
        for p in pages or []:
            try:
                numbers.append(int(p))
            except (TypeError, ValueError):
                continue
        context = self.state.context_pages
        if from_gemini and numbers and not set(numbers) & set(context) and all(1 <= n <= len(context) for n in numbers):
            # Gemini ha usato la numerazione dell'estratto: si riporta alle pagine originali.
            numbers = [context[n - 1] for n in numbers]
        result = []
        for n in numbers:
            if 1 <= n <= self.doc.info.page_count and n not in result:
                result.append(n)
        return result[:MAX_PREVIEW_PAGES]

    def _fallback_preview_pages(self) -> list[int]:
        pages = self.state.context_pages
        middle = [p for p in pages if p > min(pages)] or pages
        mid = len(middle) // 2
        return middle[max(0, mid - 1) : mid + 1]

    def set_preview_pages(self, pages: list[int]) -> None:
        valid = self._valid_preview_pages(pages)
        if not valid:
            raise SetupError("Nessuna pagina valida per l'anteprima.")
        if len(pages) > MAX_PREVIEW_PAGES:
            raise SetupError(f"Al massimo {MAX_PREVIEW_PAGES} pagine per l'anteprima.")
        self.state.preview_pages = valid
        self.save_state()

    def set_context_pages(self, pages: list[int]) -> None:
        if not pages:
            raise SetupError("Indica almeno una pagina.")
        if len(pages) > MAX_SELECTED_PAGES:
            raise SetupError(f"Al massimo {MAX_SELECTED_PAGES} pagine.")
        self.state.context_pages = pages
        self.save_state()

    # ---------------------------------------------------------------- anteprima
    def run_preview(self, show_progress: bool = True, control: RunControl | None = None) -> Preview:
        spec = self.load_spec()
        pages = self.state.preview_pages or self._fallback_preview_pages()
        out_dir = self.round_dir() / "anteprima"
        report, results = self.app.extractor().preview(
            self.doc, spec, self.primer_text(), pages, out_dir, show_progress=show_progress, control=control
        )
        failed = {n: msg for n, msg in report.failed.items()}
        index = assemble(spec, results, self.doc.info.source_filename, pages, failed)
        anomalies_path = write_index(index, spec, self.preview_csv_path)

        rows_per_page = {p: 0 for p in pages}
        if spec.output.page_column:
            for r in index.rows:
                p = int(r[spec.output.page_column])
                rows_per_page[p] = rows_per_page.get(p, 0) + 1
        else:
            for p, res in results.items():
                rows_per_page[p] = len(res.get("rows", []))

        comparison, note = self._compare(spec, index, pages)
        preview = Preview(report, index, self.preview_csv_path, anomalies_path, comparison, note, rows_per_page)
        self._record_preview(preview, pages)
        return preview

    def _compare(self, spec: IndexingSpec, index: AssembledIndex, pages: list[int]) -> tuple[Comparison | None, str]:
        if not self.state.reference_csv:
            return None, ""
        ref_columns, reference = read_csv_rows(Path(self.state.reference_csv))
        src_col, page_col = spec.output.source_column, spec.output.page_column
        if not (src_col and src_col in ref_columns and page_col and page_col in ref_columns):
            return None, (
                "Confronto non possibile: il riferimento non ha le colonne di pagina/file "
                f"({page_col}, {src_col}) per isolare le stesse pagine."
            )
        ref = filter_reference(reference, src_col, self.doc.info.source_filename, page_col, pages)
        if not ref:
            return None, "Il riferimento non contiene righe di questo documento per le pagine provate."
        keys = [c for c in index.columns if c in ref_columns and c not in (src_col, page_col)]
        if not keys:
            return None, "Il riferimento non ha colonne in comune con la specifica."
        return compare_rows(index.rows, ref, keys), (
            f"Confronto sulle colonne: {', '.join(keys)}. Righe del riferimento considerate: quelle "
            f"con {page_col} tra le pagine provate. Se nel riferimento le righe di una scheda su due "
            "pagine sono tutte attribuite alla prima, prova entrambe le pagine della scheda."
        )

    def _record_preview(self, preview: Preview, pages: list[int]) -> None:
        notes = self.round_notes()
        info: dict[str, Any] = {
            "pages": pages,
            "rows_per_page": {str(k): v for k, v in preview.rows_per_page.items()},
            "failed": {str(k): v for k, v in preview.report.failed.items()},
            "anomalies": [
                f"pagina {a.page}, riga {a.csv_row or '-'}, {a.column or '-'}: {a.problem}"
                + (f" ({a.value})" if a.value else "")
                for a in preview.index.anomalies
                if a.level != "info" or "nota del modello" in a.problem
            ][:MAX_ANOMALIES_IN_PROMPT],
        }
        info["fingerprint"] = self.app.extractor().fingerprint(self.load_spec(), self.primer_text())
        info["comparison_note"] = preview.comparison_note
        if preview.comparison is not None:
            c = preview.comparison
            info["comparison"] = {
                "recall": c.recall,
                "precision": c.precision,
                "missing": [" | ".join(r.values()) for r in c.missing[:20]],
                "extra": [" | ".join(r.values()) for r in c.extra[:20]],
            }
        notes["preview"] = info
        write_json(self.round_dir() / "round.json", notes)

    # ---------------------------------------------------------------- salvataggio
    def targets(self) -> tuple[Path, Path]:
        ws = self.app.workspace
        return ws.primer_path(self.state.primer_name), ws.spec_path(self.state.spec_name)

    def files_that_would_change(self) -> list[Path]:
        """File già presenti nel workspace che il salvataggio modificherebbe davvero."""
        primer_target, spec_target = self.targets()
        changed = []
        if primer_target.exists() and primer_target.read_text(encoding="utf-8") != self.primer_text():
            changed.append(primer_target)
        if spec_target.exists():
            try:
                same = load_spec(spec_target).model_dump(exclude={"examples"}) == self.load_spec().model_dump(
                    exclude={"examples"}
                )
            except SpecError:
                same = False
            if not same:
                changed.append(spec_target)
        return changed

    def save(self) -> tuple[Path, Path]:
        """Copia primer e specifica correnti nel workspace e associa il primer al documento."""
        primer_target, spec_target = self.targets()
        primer_target.parent.mkdir(parents=True, exist_ok=True)
        spec_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.primer_path, primer_target)

        data = yaml.safe_load(self.spec_text())
        csv = (data.get("examples") or {}).get("csv")
        if csv and not self.state.reference_csv and Path(csv).parent == spec_target.parent.resolve():
            data["examples"]["csv"] = Path(csv).name
        if self.state.reference_csv:
            example_copy = spec_target.with_name(f"{self.state.spec_name}_esempio.csv")
            shutil.copyfile(self.state.reference_csv, example_copy)
            data["examples"] = {**(data.get("examples") or {}), "csv": example_copy.name}
        header = (
            f"# Specifica creata con 'ragpdf setup {self.doc.id}' il {utc_now()[:10]}.\n"
            f"# Controllala con: ragpdf spec check {self.state.spec_name}\n\n"
        )
        atomic_write_text(spec_target, header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100))
        load_spec(spec_target)

        self.app.assign_primer(self.doc, self.state.primer_name)
        self.app.assign_spec(self.doc, self.state.spec_name)
        self.state.saved_at = utc_now()
        self.save_state()
        return primer_target, spec_target

    def reuse_preview(self) -> int:
        """Copia le pagine dell'ultima anteprima nell'indicizzazione vera, se le regole coincidono.

        Evita di pagare due volte le stesse pagine. Restituisce le pagine riutilizzate
        (0 se le regole sono cambiate o se esiste già un'indicizzazione con regole diverse).
        """
        from ragpdf.indexing.extractor import SpecChangedError

        preview = self.round_notes().get("preview") or {}
        out_dir = self.round_dir() / "anteprima"
        if not preview.get("fingerprint") or not out_dir.exists():
            return 0
        spec = load_spec(self.app.workspace.spec_path(self.state.spec_name))
        primer = self.app.workspace.read_primer(self.state.primer_name)
        extractor = self.app.extractor()
        if extractor.fingerprint(spec, primer) != preview["fingerprint"]:
            return 0
        try:
            return extractor.seed(self.doc, spec, primer, out_dir)
        except SpecChangedError:
            return 0
