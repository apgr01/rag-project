"""Sessione guidata di configurazione: primer e specifica costruiti insieme a Gemini.

La sessione appartiene a un **progetto** (uno o più PDF con la stessa struttura):

1. l'utente sceglie alcune **pagine significative**, anche da PDF diversi (indice, legende,
   pagine tipo): vengono inviate a Gemini come un unico estratto PDF, con la corrispondenza
   delle pagine scritta nel prompt;
2. dice, se vuole, **cosa vuole ottenere** e può indicare un **CSV di riferimento**;
3. Gemini scrive **primer e specifica** (comuni a tutti i PDF), eventuali **note per singoli
   PDF** che si discostano dagli altri, e propone delle **pagine di prova**;
4. il programma esegue un'**anteprima** con le stesse regole dell'indicizzazione vera, su
   almeno una pagina di **ogni** PDF (è così che si scopre se hanno davvero la stessa
   struttura), e la confronta con il riferimento;
5. l'utente scrive cosa non va **a parole**; Gemini aggiorna primer, specifica e note; si
   ripete;
6. alla fine tutto viene **salvato** nel workspace e associato al progetto.

Le pagine sono coppie ``(id documento, numero di pagina)``. Ogni versione ("giro") resta su
disco in ``projects/<id>/setup/rounds/NN/``. Questo modulo contiene solo la logica;
l'interazione con l'utente è in ``cli_setup.py`` e nell'interfaccia web.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ragpdf.app import App
from ragpdf.core.documents import Document
from ragpdf.core.fileio import atomic_write_text, read_json, utc_now, write_json
from ragpdf.core.library import PAGE_TAGS
from ragpdf.core.page_runner import RunControl, RunReport
from ragpdf.core.paths import EXAMPLES_DIR
from ragpdf.core.pdf_tools import build_multi_subset_pdf, labeled_mapping_text, page_mapping_text
from ragpdf.core.projects import DocumentSettings, Project
from ragpdf.core.prompts import load_prompt, render_prompt
from ragpdf.core.render import format_pages, parse_page_ranges
from ragpdf.indexing.assemble import AssembledIndex, assemble, merge
from ragpdf.indexing.compare import Comparison, compare_rows, filter_reference
from ragpdf.indexing.export import write_index
from ragpdf.indexing.spec import IndexingSpec, SpecError, load_spec, read_csv_rows
from ragpdf.primer import MAX_SELECTED_PAGES, clean_markdown, sample_pages

PageRef = tuple[str, int]          # (id del documento, pagina)

CARD_DESCRIPTION_MAX_CHARS = 1500    # descrizione della scheda nel prompt
MAX_PREVIEW_PAGES = 6
MAX_PREVIEW_PAGES_LIMIT = 12       # con molti PDF serve almeno una pagina per ciascuno
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
        "document_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"document": {"type": "string"}, "notes": {"type": "string"}},
                "required": ["document", "notes"],
            },
        },
    },
    "required": ["primer_markdown", "spec_yaml", "summary", "questions", "preview_pages", "document_notes"],
}


class SetupError(Exception):
    """Errore della sessione guidata, con messaggio per l'utente."""


# =====================================================================================
# Pagine di più documenti
# =====================================================================================
def _match_document(ref: str, docs: list[Document]) -> Document:
    key = ref.strip().lower()
    exact = [d for d in docs if key in (d.id.lower(), d.info.title.lower(), d.info.source_filename.lower())]
    if len(exact) == 1:
        return exact[0]
    partial = [d for d in docs if key in d.id.lower() or key in d.info.title.lower()]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(d.id for d in docs)
    if partial:
        raise ValueError(f"'{ref}' corrisponde a più documenti ({', '.join(d.id for d in partial)}).")
    raise ValueError(f"Documento '{ref}' non presente nel progetto (documenti: {names}).")


def parse_page_refs(text: str, docs: list[Document]) -> list[PageRef]:
    """Interpreta una selezione di pagine, anche su più documenti.

    - un solo documento: ``"1-4, 12"``;
    - più documenti: ``"kubota: 1-4, 12; lombardini: 3"`` (basta una parte del nome).
    """
    if not docs:
        raise ValueError("Il progetto non contiene documenti.")
    text = (text or "").strip()
    if not text:
        return []
    refs: list[PageRef] = []
    for segment in [s for s in text.split(";") if s.strip()]:
        if ":" in segment:
            name, ranges = segment.split(":", 1)
            doc = _match_document(name, docs)
        elif len(docs) == 1:
            doc, ranges = docs[0], segment
        else:
            raise ValueError(
                "Con più documenti indica a quale si riferiscono le pagine, es. 'kubota: 1-4, 12; lombardini: 3'."
            )
        for p in parse_page_ranges(ranges, doc.info.page_count) or []:
            if (doc.id, p) not in refs:
                refs.append((doc.id, p))
    return refs


def format_page_refs(refs: list[PageRef], docs: list[Document]) -> str:
    """Forma compatta: ``"1-4, 12"`` con un documento, ``"Kubota: 1-4; VM: 3"`` con più."""
    by_doc: dict[str, list[int]] = {}
    for doc_id, page in refs:
        by_doc.setdefault(doc_id, []).append(page)
    if len(docs) <= 1:
        return format_pages([p for pages in by_doc.values() for p in pages])
    titles = {d.id: d.info.title for d in docs}
    return "; ".join(f"{titles.get(d, d)}: {format_pages(pages)}" for d, pages in by_doc.items())


def default_context_refs(docs: list[Document]) -> list[PageRef]:
    """Proposta di pagine da mostrare a Gemini.

    Con un documento: le prime pagine (indice, legende) più alcune distribuite. Con più
    documenti: lo stesso per il primo, più due pagine centrali di ciascuno degli altri.
    """
    if not docs:
        return []
    first = docs[0]
    total = 14 if len(docs) == 1 else 8
    refs = [(first.id, p) for p in sample_pages(first.info.page_count, first=min(6, total), total=total)]
    for doc in docs[1:]:
        middle = max(1, doc.info.page_count // 2)
        for p in (middle, middle + 1):
            if p <= doc.info.page_count:
                refs.append((doc.id, p))
    return refs[:MAX_SELECTED_PAGES]


def _as_refs(raw: Any) -> list[PageRef]:
    return [(str(r[0]), int(r[1])) for r in raw or []]


# =====================================================================================
# Stato e risultati
# =====================================================================================
@dataclass
class SetupState:
    project_id: str
    primer_name: str
    spec_name: str
    context_pages: list[PageRef]
    preview_pages: list[PageRef] = field(default_factory=list)
    goal: str = ""
    reference_csv: str | None = None
    current_round: int = 0
    last_round: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    saved_at: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any], project: Project) -> SetupState:
        data = dict(data)
        # Sessioni create prima dei progetti: un documento, pagine come numeri.
        doc_id = data.pop("doc_id", None) or (project.info.documents[0] if project.info.documents else "")
        data.setdefault("project_id", project.id)
        for key in ("context_pages", "preview_pages"):
            data[key] = [(doc_id, int(x)) if isinstance(x, int) else (str(x[0]), int(x[1]))
                         for x in data.get(key) or []]
        return cls(**data)


@dataclass
class Preview:
    report: RunReport
    index: AssembledIndex
    csv_path: Path
    anomalies_path: Path | None
    comparison: Comparison | None = None
    comparison_note: str = ""
    rows_per_page: dict[PageRef, int] = field(default_factory=dict)


# =====================================================================================
# Sessione
# =====================================================================================
class SetupSession:
    def __init__(self, app: App, project: Project, state: SetupState):
        self.app = app
        self.project = project
        self.state = state

    # ------------------------------------------------------------------ documenti
    @property
    def documents(self) -> list[Document]:
        return self.app.project_documents(self.project)

    def document(self, doc_id: str) -> Document:
        for d in self.documents:
            if d.id == doc_id:
                return d
        raise SetupError(f"Il documento '{doc_id}' non fa più parte del progetto.")

    @property
    def single(self) -> bool:
        return len(self.project.info.documents) <= 1

    def label(self, ref: PageRef) -> str:
        doc = self.document(ref[0])
        return f"pagina {ref[1]}" if self.single else f"{doc.info.title} p.{ref[1]}"

    # ------------------------------------------------------------------ percorsi
    @staticmethod
    def folder_for(project: Project) -> Path:
        return project.folder / "setup"

    @property
    def folder(self) -> Path:
        return self.folder_for(self.project)

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
    def load(cls, app: App, project: Project) -> SetupSession | None:
        path = cls.folder_for(project) / "session.json"
        if not path.exists():
            return None
        return cls(app, project, SetupState.from_json(read_json(path), project))

    def save_state(self) -> None:
        data = asdict(self.state)
        data["context_pages"] = [list(r) for r in self.state.context_pages]
        data["preview_pages"] = [list(r) for r in self.state.preview_pages]
        write_json(self.folder / "session.json", data)

    @classmethod
    def new(
        cls,
        app: App,
        project: Project,
        context_pages: list[PageRef],
        goal: str = "",
        reference_csv: Path | None = None,
        primer_name: str | None = None,
        spec_name: str | None = None,
    ) -> SetupSession:
        if not app.project_documents(project):
            raise SetupError("Il progetto non contiene documenti: aggiungi almeno un PDF.")
        if not context_pages:
            raise SetupError("Indica almeno una pagina da mostrare a Gemini.")
        if len(context_pages) > MAX_SELECTED_PAGES:
            raise SetupError(
                f"Hai indicato {len(context_pages)} pagine: il massimo è {MAX_SELECTED_PAGES}. "
                "Scegli le più rappresentative (indice, legende, alcune pagine tipo)."
            )
        if reference_csv is not None and not Path(reference_csv).is_file():
            raise SetupError(f"CSV di riferimento non trovato: {reference_csv}")
        folder = cls.folder_for(project)
        if folder.exists():
            shutil.rmtree(folder)  # una nuova sessione sostituisce quella vecchia
        state = SetupState(
            project_id=project.id,
            primer_name=primer_name or project.info.primer or project.id,
            spec_name=spec_name or project.info.spec or project.id,
            context_pages=[(str(d), int(p)) for d, p in context_pages],
            goal=goal.strip(),
            reference_csv=str(Path(reference_csv).resolve()) if reference_csv else None,
        )
        session = cls(app, project, state)
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
        notes = read_json(path) if path.exists() else {}
        return self._upgrade_round_notes(notes)

    def _upgrade_round_notes(self, notes: dict[str, Any]) -> dict[str, Any]:
        """Versioni salvate prima dei progetti: pagine come numeri di un solo documento."""
        preview = notes.get("preview")
        if not preview or not isinstance(preview.get("rows_per_page"), dict):
            return notes
        doc_id = self.project.info.documents[0] if self.project.info.documents else ""
        rows = preview["rows_per_page"]
        preview["pages"] = [[doc_id, int(p)] for p in preview.get("pages") or rows]
        preview["rows_per_page"] = [
            {"document": doc_id, "page": int(p), "label": f"pagina {p}", "rows": n} for p, n in rows.items()
        ]
        if preview.get("fingerprint") and "fingerprints" not in preview:
            preview["fingerprints"] = {doc_id: preview.pop("fingerprint")}
        # anteprima vecchia: i risultati stavano in anteprima/ invece che in anteprima/<documento>/
        old_dir = self.round_dir(notes.get("round")) / "anteprima"
        if old_dir.exists() and any(old_dir.glob("page-*.json")) and not (old_dir / doc_id).exists():
            (old_dir / doc_id).mkdir(parents=True)
            for f in old_dir.glob("*.json"):
                f.rename(old_dir / doc_id / f.name)
        return notes

    def document_notes(self) -> dict[str, str]:
        """Note per singolo documento della versione corrente (o quelle già salvate nel progetto)."""
        if self.state.current_round:
            notes = self.round_notes().get("document_notes")
            if notes is not None:
                return dict(notes)
        return {d: s.notes for d, s in self.project.info.settings.items() if s.notes.strip()}

    def document_spec(self, doc: Document, base: IndexingSpec | None = None) -> IndexingSpec:
        """Specifica della versione corrente con le note di quel documento (senza pagine proprie)."""
        base = base or self.load_spec()
        return base.for_document(self.document_notes().get(doc.id, ""), None, doc.info.title)

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
        docs = {d.id: d for d in self.documents}
        if self.single:
            doc = next(iter(docs.values()))
            note = page_mapping_text([p for _, p in self.state.context_pages], doc.info.page_count)
        else:
            entries = [(f"{docs[d].info.title} [{d}]", p) for d, p in self.state.context_pages if d in docs]
            note = labeled_mapping_text(entries, len(docs))
        return note + "\nNel testo che scrivi indica sempre i numeri di pagina dei documenti ORIGINALI."

    def _documents_block(self) -> str:
        docs = self.documents
        parts = []
        if len(docs) > 1:
            lines = [f"- [{d.id}] {d.info.title} ({d.info.page_count} pagine)" for d in docs]
            parts.append(
                "\n## DOCUMENTI DEL PROGETTO\nTutti i documenti condividono primer e specifica (stesse colonne). "
                "Se un documento si discosta dagli altri (layout, sigle, posizione dei dati, pagine da "
                "ignorare), scrivi le istruzioni particolari in \"document_notes\" per quel documento, "
                "indicandolo con l'id tra parentesi quadre, invece di complicare le regole comuni:\n"
                + "\n".join(lines) + "\n"
            )
        cards = [c for c in (self._card_text(d) for d in docs) if c]
        if cards:
            title = "SCHEDE DEI DOCUMENTI" if len(docs) > 1 else "SCHEDA DEL DOCUMENTO"
            parts.append(
                f"\n## {title} (dalla biblioteca)\nCiò che l'utente sa già dei PDF: usalo per il primer "
                "(struttura, sezioni, convenzioni) e per le note dei singoli documenti. I numeri di pagina "
                "sono quelli dei documenti originali.\n\n" + "\n\n".join(cards) + "\n"
            )
        return "".join(parts)

    def _card_text(self, doc: Document) -> str:
        """La scheda della biblioteca di un documento, per il prompt (vuota se non c'è niente)."""
        card = self.app.library.card(doc)
        lines = []
        description = card.description.strip()
        if description:
            if len(description) > CARD_DESCRIPTION_MAX_CHARS:
                description = description[:CARD_DESCRIPTION_MAX_CHARS].rstrip() + " [...]"
            lines.append(f"- Descrizione: {description}")
        if card.fields:
            lines.append("- Dati presenti: " + "; ".join(
                f.name + (f" ({f.description})" if f.description else "") for f in card.fields))
        for c in card.collections:
            pages = f"pagina {c.start}" if c.start == c.end else f"pagine {c.start}-{c.end}"
            label = PAGE_TAGS[c.tag][0]
            lines.append(f"- {pages}: {label}" + (f" — {c.comment}" if c.comment else ""))
        if not lines:
            return ""
        return f"### [{doc.id}] {doc.info.title} ({doc.info.page_count} pagine)\n" + "\n".join(lines)

    def _notes_block(self) -> str:
        notes = self.document_notes()
        if not notes:
            return "(nessuna)"
        titles = {d.id: d.info.title for d in self.documents}
        return "\n".join(f"- [{d}] {titles.get(d, d)}: {n}" for d, n in notes.items())

    def _goal_text(self) -> str:
        table = self._table_text()
        goal = self.state.goal.strip()
        if table:
            return table + (f"\n\nAltre indicazioni per questa configurazione:\n{goal}" if goal else "")
        return goal or (
            "L'utente non ha indicato cosa vuole ottenere: proponi tu l'indicizzazione più utile "
            "per questo documento (ad esempio, per un catalogo di ricambi, una riga per ogni codice "
            "articolo con i dati che permettono di cercarlo)."
        )

    def _table_text(self) -> str:
        """La tabella decisa dall'utente nelle impostazioni globali del progetto (vincolante)."""
        info = self.project.info
        if not info.has_table:
            return ""
        lines = ["La tabella che l'utente vuole è già decisa (impostazioni del progetto):"]
        if info.description.strip():
            lines.append(f"- Descrizione: {info.description.strip()}")
        if info.row.strip():
            lines.append(f"- Ogni riga rappresenta: {info.row.strip()} (usalo per `row`)")
        if info.fields:
            lines.append("- Colonne OBBLIGATORIE: in `columns` usa ESATTAMENTE questi nomi, in quest'ordine, "
                         "senza aggiungerne né toglierne; completa tu descrizione e dettagli tecnici di ciascuna:")
            lines += [f"  {i}. {f.get('name', '')}" + (f": {f['description']}" if f.get("description") else "")
                      for i, f in enumerate(info.fields, 1)]
        if info.instructions.strip():
            lines.append(f"- Altre indicazioni: {info.instructions.strip()}")
        return "\n".join(lines)

    def _preview_block(self) -> str:
        preview = self.round_notes().get("preview")
        if not preview:
            return "(nessuna prova eseguita)"
        parts = []
        per_page = ", ".join(f"{p['label']}: {p['rows']} righe" for p in preview["rows_per_page"])
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

    def build_initial_prompt(self) -> str:
        """Il prompt del primo giro, esattamente come verrebbe inviato (senza inviarlo)."""
        template = (EXAMPLES_DIR / "specs" / "base.yaml").read_text(encoding="utf-8")
        return render_prompt(
            load_prompt("setup_initial"),
            pages_note=self._pages_note(),
            documents=self._documents_block(),
            goal=self._goal_text(),
            example=self._example_block(),
            spec_template=template.strip(),
        )

    def build_revise_prompt(self, feedback: str) -> str:
        """Il prompt di revisione, esattamente come verrebbe inviato (senza inviarlo)."""
        return render_prompt(
            load_prompt("setup_revise"),
            pages_note=self._pages_note(),
            documents=self._documents_block(),
            goal=self._goal_text(),
            example=self._example_block(),
            primer=self.primer_text().strip(),
            spec=self.spec_text().strip(),
            document_notes=self._notes_block(),
            preview=self._preview_block(),
            history=self._history_block(),
            feedback=feedback.strip(),
        )

    def attachment_description(self) -> str:
        return (f"estratto PDF di {len(self.state.context_pages)} pagine "
                f"({format_page_refs(self.state.context_pages, self.documents)})")

    # ----------------------------------------------------------- chiamate Gemini
    def _ask_gemini(self, prompt: str) -> dict[str, Any]:
        docs = {d.id: d for d in self.documents}
        entries = [(docs[d].pdf_path, p) for d, p in self.state.context_pages if d in docs]
        with tempfile.TemporaryDirectory() as tmp:
            subset = build_multi_subset_pdf(entries, Path(tmp) / "estratto.pdf")
            response = self.app.client().generate_json(prompt, RESPONSE_SCHEMA, pdf=subset)
        if not isinstance(response, dict) or not response.get("spec_yaml") or not response.get("primer_markdown"):
            raise SetupError("Gemini ha restituito una risposta incompleta. Riprova.")
        return response

    def _parse_document_notes(self, raw: Any) -> dict[str, str]:
        """Note per documento restituite da Gemini: ``{id: testo}`` (testo vuoto = da togliere).

        Gemini indica solo le note da aggiungere, cambiare o togliere: una nota non citata
        resta com'è, così una dimenticanza del modello non cancella le note dell'utente.
        """
        ids = {d.id for d in self.documents}
        notes: dict[str, str] = {}
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("document", "")).strip()
            match = re.search(r"\[([^\]]+)\]", ref)
            doc_id = match.group(1) if match else ref
            if doc_id not in ids:
                try:
                    doc_id = _match_document(ref, self.documents).id
                except ValueError:
                    continue
            notes[doc_id] = str(item.get("notes", "")).strip()
        return notes

    def _store_round(self, response: dict[str, Any], feedback: str, origin: str) -> int:
        """Valida la risposta, la salva come nuovo giro e lo rende corrente."""
        n = self.state.last_round + 1
        folder = self.round_dir(n)
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / "primer.md", clean_markdown(response["primer_markdown"]))
        self._write_spec(folder / "spec.yaml", response["spec_yaml"], response.get("_base_dir"))
        if "_notes_replace" in response:            # modifica manuale: elenco completo
            notes = {d: t for d, t in response["_notes_replace"].items() if t.strip()}
        else:
            notes = self.document_notes()
            for doc_id, text in self._parse_document_notes(response.get("document_notes")).items():
                if text:
                    notes[doc_id] = text
                else:
                    notes.pop(doc_id, None)
        suggested = self._preview_from_extract(response.get("preview_pages"))
        write_json(
            folder / "round.json",
            {
                "round": n,
                "origin": origin,
                "feedback": feedback,
                "summary": str(response.get("summary", "")).strip(),
                "questions": [str(q) for q in response.get("questions") or []],
                "document_notes": notes,
                "suggested_preview_pages": [list(r) for r in suggested],
                "created_at": utc_now(),
            },
        )
        self.state.last_round = n
        self.state.current_round = n
        self.state.history.append({"round": n, "feedback": feedback, "origin": origin})
        if suggested:
            self.state.preview_pages = suggested
        self.state.preview_pages = self._ensure_each_document(self.state.preview_pages)
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
            for doc in self.documents:
                try:
                    parse_page_ranges(spec.pages, doc.info.page_count)
                except ValueError as e:
                    raise SpecError(f"pages: {e} (documento {doc.info.title})") from e

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
            "document_notes": [],
        }
        try:
            return self._store_round(response, feedback="", origin=f"da {primer_name}/{spec_name}")
        except SpecError as e:
            self._discard_partial_round()
            raise SetupError(f"La specifica '{spec_name}' non è valida:\n{e}") from e

    def revise(self, feedback: str) -> int:
        """Nuovo giro: Gemini applica le osservazioni dell'utente."""
        if not feedback.strip():
            raise SetupError("Scrivi cosa vuoi cambiare.")
        return self._generate(self.build_revise_prompt(feedback), feedback=feedback.strip(), origin="revisione")

    def manual_edit_done(
        self,
        primer_text: str | None = None,
        spec_text: str | None = None,
        document_notes: dict[str, str] | None = None,
    ) -> int:
        """Registra una modifica fatta a mano come nuovo giro (dopo averla validata)."""
        notes = document_notes if document_notes is not None else self.document_notes()
        response = {
            "primer_markdown": primer_text if primer_text is not None else self.primer_text(),
            "spec_yaml": spec_text if spec_text is not None else self.spec_text(),
            "summary": "Modifica manuale.",
            "_base_dir": self.round_dir(),
            "questions": [],
            "preview_pages": [],
            "_notes_replace": notes,
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
    def _preview_from_extract(self, numbers: Any) -> list[PageRef]:
        """Gemini indica le pagine di prova con la numerazione dell'estratto: univoca anche con più PDF."""
        context = self.state.context_pages
        result: list[PageRef] = []
        for raw in numbers or []:
            try:
                i = int(raw)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(context) and context[i - 1] not in result:
                result.append(context[i - 1])
        return result[:MAX_PREVIEW_PAGES]

    def _ensure_each_document(self, refs: list[PageRef]) -> list[PageRef]:
        """Almeno una pagina di prova per ogni documento: è così che si vede se hanno la stessa struttura."""
        refs = [r for r in refs if r[0] in self.project.info.documents]
        for doc in self.documents:
            if any(d == doc.id for d, _ in refs):
                continue
            own = [r for r in self.state.context_pages if r[0] == doc.id]
            # una pagina "tipo": tra quelle scelte, preferendo quelle dopo le prime (indici, legende)
            typical = [r for r in own if r[1] > 3] or own
            refs.append(typical[len(typical) // 2] if typical else (doc.id, max(1, doc.info.page_count // 2)))
        if not refs and self.state.context_pages:
            ctx = self.state.context_pages
            refs = [ctx[len(ctx) // 2]]
        return refs[:MAX_PREVIEW_PAGES_LIMIT]

    def set_preview_pages(self, refs: list[PageRef]) -> None:
        refs = [r for r in refs if r[0] in self.project.info.documents]
        if not refs:
            raise SetupError("Nessuna pagina valida per l'anteprima.")
        limit = max(MAX_PREVIEW_PAGES, len(self.project.info.documents))
        if len(refs) > min(limit, MAX_PREVIEW_PAGES_LIMIT):
            raise SetupError(f"Al massimo {min(limit, MAX_PREVIEW_PAGES_LIMIT)} pagine per l'anteprima.")
        self.state.preview_pages = refs
        self.save_state()

    def set_context_pages(self, refs: list[PageRef]) -> None:
        if not refs:
            raise SetupError("Indica almeno una pagina.")
        if len(refs) > MAX_SELECTED_PAGES:
            raise SetupError(f"Al massimo {MAX_SELECTED_PAGES} pagine.")
        self.state.context_pages = refs
        self.save_state()

    # ---------------------------------------------------------------- anteprima
    def run_preview(self, show_progress: bool = True, control: RunControl | None = None) -> Preview:
        base = self.load_spec()
        primer = self.primer_text()
        refs = self.state.preview_pages = self._ensure_each_document(self.state.preview_pages)
        self.save_state()
        out_root = self.round_dir() / "anteprima"
        extractor = self.app.extractor()

        parts: list[tuple[Document, AssembledIndex]] = []
        report = RunReport()
        fingerprints: dict[str, str] = {}
        for doc in self.documents:
            pages = sorted(p for d, p in refs if d == doc.id)
            if not pages:
                continue
            spec = self.document_spec(doc, base)
            r, results = extractor.preview(doc, spec, primer, pages, out_root / doc.id,
                                           show_progress=show_progress, control=control)
            report.requested += r.requested
            report.succeeded += r.succeeded
            report.not_attempted += r.not_attempted
            report.stopped_reason = report.stopped_reason or r.stopped_reason
            for n, msg in r.failed.items():
                report.failed[f"{doc.id}:{n}"] = msg  # type: ignore[index]
            index = assemble(spec, results, doc.info.source_filename, pages, r.failed)
            parts.append((doc, index))
            fingerprints[doc.id] = extractor.fingerprint(spec, primer)

        merged = merge(base, [i for _, i in parts], deduplicate=False)
        merged.anomalies = [a for _, i in parts for a in i.anomalies]
        anomalies_path = write_index(merged, base, self.preview_csv_path)

        rows_per_page: dict[PageRef, int] = {r: 0 for r in refs}
        for doc, index in parts:
            for p in {p for d, p in refs if d == doc.id}:
                if base.output.page_column:
                    column = base.output.page_column
                    rows_per_page[(doc.id, p)] = sum(1 for row in index.rows if row.get(column) == str(p))
                else:
                    rows_per_page[(doc.id, p)] = len(index.rows)

        comparison, note = self._compare(base, parts)
        preview = Preview(report, merged, self.preview_csv_path, anomalies_path, comparison, note, rows_per_page)
        self._record_preview(preview, parts, fingerprints)
        return preview

    def _compare(
        self, spec: IndexingSpec, parts: list[tuple[Document, AssembledIndex]]
    ) -> tuple[Comparison | None, str]:
        if not self.state.reference_csv:
            return None, ""
        ref_columns, reference = read_csv_rows(Path(self.state.reference_csv))
        src_col, page_col = spec.output.source_column, spec.output.page_column
        if not (src_col and src_col in ref_columns and page_col and page_col in ref_columns):
            return None, (
                "Confronto non possibile: il riferimento non ha le colonne di pagina/file "
                f"({page_col}, {src_col}) per isolare le stesse pagine."
            )
        ref_rows: list[dict[str, str]] = []
        produced: list[dict[str, str]] = []
        for doc, index in parts:
            pages = sorted(p for d, p in self.state.preview_pages if d == doc.id)
            ref_rows += filter_reference(reference, src_col, doc.info.source_filename, page_col, pages)
            produced += index.rows
        if not ref_rows:
            return None, "Il riferimento non contiene righe di questi documenti per le pagine provate."
        keys = [c for c in spec.output_columns if c in ref_columns and c not in (src_col, page_col)]
        if not keys:
            return None, "Il riferimento non ha colonne in comune con la specifica."
        return compare_rows(produced, ref_rows, keys), (
            f"Confronto sulle colonne: {', '.join(keys)}. Righe del riferimento considerate: quelle "
            f"degli stessi file con {page_col} tra le pagine provate. Se nel riferimento le righe di una "
            "scheda su due pagine sono tutte attribuite alla prima, prova entrambe le pagine della scheda."
        )

    def _record_preview(
        self, preview: Preview, parts: list[tuple[Document, AssembledIndex]], fingerprints: dict[str, str]
    ) -> None:
        notes = self.round_notes()
        anomalies = []
        for doc, index in parts:
            prefix = "" if self.single else f"{doc.info.title}, "
            for a in index.anomalies:
                if a.level == "info" and "nota del modello" not in a.problem:
                    continue
                anomalies.append(f"{prefix}pagina {a.page}, riga {a.csv_row or '-'}, {a.column or '-'}: {a.problem}"
                                 + (f" ({a.value})" if a.value else ""))
        info: dict[str, Any] = {
            "pages": [list(r) for r in preview.rows_per_page],
            "rows_per_page": [
                {"document": d, "page": p, "label": self.label((d, p)), "rows": n}
                for (d, p), n in preview.rows_per_page.items()
            ],
            "failed": {str(k): v for k, v in preview.report.failed.items()},
            "anomalies": anomalies[:MAX_ANOMALIES_IN_PROMPT],
            "fingerprints": fingerprints,
            "comparison_note": preview.comparison_note,
        }
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
        """Copia primer e specifica nel workspace e li associa al progetto (con le note per documento)."""
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
            f"# Specifica creata con la configurazione guidata del progetto '{self.project.id}' "
            f"il {utc_now()[:10]}.\n# Controllala con: ragpdf spec check {self.state.spec_name}\n\n"
        )
        atomic_write_text(spec_target, header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100))
        load_spec(spec_target)

        # note per documento: quelle della versione salvata; le pagine proprie restano
        notes = self.document_notes()
        for doc in self.documents:
            current = self.project.settings_for(doc.id)
            self.project.set_settings(doc.id, DocumentSettings(notes=notes.get(doc.id, ""), pages=current.pages))
        self.app.assign_project_rules(self.project, self.state.primer_name, self.state.spec_name)

        self.state.saved_at = utc_now()
        self.save_state()
        return primer_target, spec_target

    def reuse_preview(self) -> int:
        """Copia le pagine dell'ultima anteprima nell'indicizzazione vera, se le regole coincidono.

        Evita di pagare due volte le stesse pagine. Restituisce le pagine riutilizzate
        (0 per i documenti con regole cambiate o con un'indicizzazione fatta con regole diverse).
        """
        from ragpdf.indexing.extractor import SpecChangedError

        preview = self.round_notes().get("preview") or {}
        fingerprints = preview.get("fingerprints") or {}
        out_root = self.round_dir() / "anteprima"
        if not fingerprints or not out_root.exists():
            return 0
        base = load_spec(self.app.workspace.spec_path(self.state.spec_name))
        primer = self.app.workspace.read_primer(self.state.primer_name)
        extractor = self.app.extractor()
        reused = 0
        for doc in self.documents:
            source = out_root / doc.id
            if doc.id not in fingerprints or not source.exists():
                continue
            spec = self.app.document_spec(self.project, doc, base)
            if extractor.fingerprint(spec, primer) != fingerprints[doc.id]:
                continue
            try:
                reused += extractor.seed(doc, spec, primer, source)
            except SpecChangedError:
                continue
        return reused
