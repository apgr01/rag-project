"""Interfaccia a riga di comando: ``ragpdf <comando>``.

Flusso consigliato per indicizzare un catalogo::

    ragpdf doctor                              # verifica che sia tutto pronto
    ragpdf setup "Catalogo.pdf"                # configurazione guidata con anteprima
    ragpdf index catalogo                      # indicizzazione completa -> CSV

Flusso manuale, passo per passo::

    ragpdf add "Catalogo.pdf"                  # aggiunge il documento (e chiede del primer)
    ragpdf spec new ricambi                    # crea la specifica da completare
    ragpdf index catalogo --spec ricambi       # estrae le righe ed esporta il CSV
    ragpdf export catalogo --spec ricambi      # rigenera il CSV senza chiamare Gemini

Ogni comando ha il suo aiuto: ``ragpdf <comando> --help``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from ragpdf import __version__, ui
from ragpdf.app import App, PrimerMissingError
from ragpdf.core.config import ConfigError
from ragpdf.core.documents import Document, DocumentError, DuplicateDocumentError
from ragpdf.core.gemini import GeminiError, load_api_keys
from ragpdf.core.manifest import DONE, FAILED, Manifest
from ragpdf.core.page_runner import RunReport
from ragpdf.core.paths import ENV_FILE, EXAMPLES_DIR, PROJECT_ROOT
from ragpdf.indexing.extractor import SpecChangedError, selected_pages
from ragpdf.indexing.prompt import PromptBuilder
from ragpdf.indexing.spec import SpecError
from ragpdf.setup_session import SetupError

SPEC_TEMPLATES_DIR = EXAMPLES_DIR / "specs"


# =====================================================================================
# Utility comuni
# =====================================================================================
def _print_report(report: RunReport, what: str) -> None:
    if report.requested == 0:
        ui.ok(f"{what}: nessuna pagina da elaborare (già tutte completate).")
        return
    ui.info(
        f"{what}: {report.succeeded} pagine completate, {len(report.failed)} fallite, "
        f"{report.not_attempted} non tentate (su {report.requested})."
    )
    if report.stopped_reason:
        ui.warn(f"Elaborazione interrotta: {report.stopped_reason}")
    if report.stopped_reason or report.failed or report.not_attempted:
        ui.hint("Rilancia lo stesso comando per riprendere: le pagine già fatte non vengono ripetute.")


def _ensure_primer(app: App, doc: Document, assume_yes: bool) -> str | None:
    """Restituisce il testo del primer; se manca, aiuta l'utente a procurarselo.

    Restituisce ``None`` se l'utente sceglie di annullare.
    """
    try:
        return app.primer_text(doc)
    except PrimerMissingError:
        pass
    except FileNotFoundError as e:
        ui.warn(str(e))

    ui.warn(f"Il documento '{doc.id}' non ha un primer (la descrizione di com'è fatto il documento).")
    ui.info("Senza primer Gemini interpreta ogni pagina senza conoscere il resto del documento:")
    ui.info("i risultati sono di solito meno precisi.")

    ui.hint(f"Consigliato: interrompi e usa la configurazione guidata  ragpdf setup {doc.id}")
    options = [("generate", "Genera ora una bozza con Gemini")]
    if app.workspace.list_primers():
        options.append(("existing", "Usa un primer già presente"))
    options += [("none", "Prosegui senza primer"), ("cancel", "Annulla")]
    default = "generate" if assume_yes else "cancel"
    choice = ui.choose("Come vuoi procedere?", options, default=default)

    if choice == "generate":
        name = _generate_primer(app, doc, name=None, force=False)
        if name and ui.interactive():
            ui.info(f"Puoi rivedere il primer ora in: {app.workspace.primer_path(name)}")
            ui.ask_yes_no("Premi invio quando sei pronto per continuare", default=True)
        return app.primer_text(doc) if name else None
    if choice == "existing":
        names = app.workspace.list_primers()
        picked = ui.choose("Quale primer?", [(n, n) for n in names])
        if picked:
            app.assign_primer(doc, picked)
            return app.primer_text(doc)
        return None
    if choice == "none":
        return ""
    if not ui.interactive():
        ui.hint(f"Genera un primer con: ragpdf primer generate {doc.id}")
    return None


def _generate_primer(app: App, doc: Document, name: str | None, force: bool, pages: str | None = None) -> str | None:
    from ragpdf.core.render import parse_page_ranges
    from ragpdf.primer import generate_primer

    name = name or doc.id
    path = app.workspace.primer_path(name)
    if path.exists() and not force:
        if not ui.ask_yes_no(f"Il primer '{name}' esiste già. Sovrascriverlo?", default=False):
            ui.info("Uso il primer esistente.")
            app.assign_primer(doc, name)
            return name

    selected = parse_page_ranges(pages, doc.info.page_count) if pages else None
    ui.info(f"Genero la bozza del primer per '{doc.id}' (può richiedere qualche minuto)...")
    text, what = generate_primer(doc, app.client(), selected)
    path.write_text(text, encoding="utf-8")
    app.assign_primer(doc, name)
    ui.ok(f"Bozza del primer salvata (Gemini ha ricevuto: {what}): {path}")
    ui.hint("Rileggila e correggila: la qualità del primer incide direttamente sui risultati.")
    ui.hint("Controlla soprattutto la sezione 'Punti da verificare', se presente.")
    ui.hint(f"Per una revisione guidata con anteprima dei risultati: ragpdf setup {doc.id}")
    return name


def _resolve_docs(app: App, refs: list[str], use_all: bool) -> list[Document]:
    if use_all:
        docs = app.documents.list()
        if not docs:
            raise DocumentError("Il workspace non contiene documenti. Aggiungine uno con 'ragpdf add'.")
        return docs
    if not refs:
        raise DocumentError("Indica almeno un documento (oppure --all per tutti).")
    return [app.documents.get(r) for r in refs]


def _resolve_spec_name(app: App, given: str | None) -> str:
    if given:
        return given
    specs = app.workspace.list_specs()
    if len(specs) == 1:
        ui.info(f"Uso l'unica specifica presente: '{specs[0]}'.")
        return specs[0]
    if not specs:
        raise SpecError(
            "Non ci sono specifiche di indicizzazione.\n"
            "Creane una con: ragpdf spec new <nome>   (es. ragpdf spec new ricambi)"
        )
    picked = ui.choose("Quale specifica vuoi usare?", [(s, s) for s in specs])
    if not picked:
        raise SpecError("Indica la specifica con --spec <nome>. Disponibili: " + ", ".join(specs))
    return picked


def _print_export(app: App, result, doc_label: str) -> None:
    s = result.index.stats
    ui.ok(f"CSV di '{doc_label}': {result.csv_path}")
    ui.info(
        f"  righe: {s.get('righe_finali', 0)}   pagine con righe: {s.get('pagine_con_righe', 0)}"
        f"/{s.get('pagine_previste', 0)}   duplicati rimossi: {s.get('duplicati_rimossi', 0)}"
        f"   valori ereditati dalla pagina precedente: {s.get('valori_ereditati', 0)}"
    )
    if result.stale:
        ui.warn("La specifica, il primer o il modello sono cambiati dopo l'estrazione: il CSV usa i "
                "risultati ottenuti con le regole precedenti.")
        ui.hint(f"Per applicare le nuove regole rilancia: ragpdf index {doc_label} --spec <nome>")
    if s.get("pagine_da_elaborare"):
        ui.info(f"  pagine non ancora elaborate: {s['pagine_da_elaborare']} "
                f"(per completare: ragpdf index {doc_label} senza --pages)")
    if s.get("errori") or s.get("avvisi"):
        ui.warn(f"{s.get('errori', 0)} errori e {s.get('avvisi', 0)} avvisi da controllare: {result.anomalies_path}")
    elif result.anomalies_path:
        ui.info(f"  note e informazioni: {result.anomalies_path}")


# =====================================================================================
# Comandi
# =====================================================================================
def cmd_doctor(app: App, args: argparse.Namespace) -> int:
    ui.title("Verifica dell'ambiente")
    problems = 0

    ui.info(f"Versione ragpdf: {__version__}   Python: {sys.version.split()[0]}")
    ui.info(f"Cartella del progetto: {PROJECT_ROOT}")
    ui.info(f"Workspace dati:        {app.workspace.root}")

    if app.simulated:
        ui.warn("Modalità SIMULATA attiva (RAGPDF_FAKE_GEMINI): Gemini non verrà chiamato.")
    keys = load_api_keys()
    if keys:
        parallel = app.config.indexing.max_workers or len(keys)
        ui.ok(f"Chiavi API Gemini trovate: {len(keys)} (richieste parallele: {parallel})")
    elif not app.simulated:
        ui.error(f"Nessuna chiave API Gemini. Crea il file {ENV_FILE.name} partendo da .env.example.")
        ui.hint("Le chiavi si ottengono gratuitamente su https://aistudio.google.com/apikey")
        problems += 1

    ui.info(f"Modello: {app.config.gemini.model}   DPI rendering: {app.config.render.dpi}   "
            f"richieste/minuto per chiave: {app.config.gemini.requests_per_minute_per_key}")

    docs = app.documents.list()
    ui.info(f"Documenti: {len(docs)}   Primer: {len(app.workspace.list_primers())}   "
            f"Specifiche: {len(app.workspace.list_specs())}")
    for d in docs:
        if not d.info.primer:
            ui.warn(f"'{d.id}' non ha un primer (ragpdf primer generate {d.id}).")

    if args.online and keys and not app.simulated:
        ui.info("Provo una richiesta a Gemini...")
        try:
            reply = app.client().generate_text("Rispondi solo con la parola: pronto")
            ui.ok(f"Gemini risponde: {reply[:40]!r}")
        except GeminiError as e:
            ui.error(str(e))
            problems += 1

    if problems:
        ui.error(f"Trovati {problems} problemi da risolvere.")
        return 1
    ui.ok("Tutto pronto.")
    if not args.online:
        ui.hint("Per provare anche una richiesta reale a Gemini: ragpdf doctor --online")
    return 0


def cmd_add(app: App, args: argparse.Namespace) -> int:
    added: list[Document] = []
    for pdf in args.pdf:
        try:
            doc = app.documents.add(Path(pdf), doc_id=args.id if len(args.pdf) == 1 else None)
        except DuplicateDocumentError as e:
            ui.warn(str(e))
            continue
        ui.ok(f"Aggiunto '{doc.id}' ({doc.info.page_count} pagine).")
        added.append(doc)

    for doc in added:
        if args.primer:
            app.assign_primer(doc, args.primer)
            ui.ok(f"Primer '{args.primer}' associato a '{doc.id}'.")
        elif ui.interactive() and not args.no_primer:
            _ensure_primer(app, doc, assume_yes=False)
        else:
            ui.hint(f"Prossimo passo consigliato: ragpdf setup {doc.id}   (configurazione guidata)")
    return 0 if added or not args.pdf else 1


def cmd_list(app: App, args: argparse.Namespace) -> int:
    docs = app.documents.list()
    if not docs:
        ui.info("Il workspace non contiene documenti.")
        ui.hint('Aggiungine uno con: ragpdf add "percorso/del/file.pdf"')
        return 0
    rows = []
    for d in docs:
        progress = []
        for spec_name in d.list_extractions():
            m = Manifest.load_or_create(d.extraction_dir(spec_name) / "manifest.json", d.info.page_count)
            c = m.counts()
            progress.append(f"{spec_name} {c.get(DONE, 0)}/{d.info.page_count}"
                            + (f" ({c[FAILED]} fallite)" if c.get(FAILED) else ""))
        rows.append([d.id, d.info.page_count, d.info.primer or "-", ", ".join(progress) or "-"])
    ui.table(["documento", "pagine", "primer", "indicizzazioni (pagine fatte)"], rows)
    return 0


def cmd_remove(app: App, args: argparse.Namespace) -> int:
    doc = app.documents.get(args.doc)
    if not ui.ask_yes_no(f"Eliminare '{doc.id}' e tutti i suoi risultati?", default=False, assume_yes=args.yes):
        ui.info("Nessuna modifica.")
        return 0
    app.documents.remove(doc)
    ui.ok(f"Documento '{doc.id}' eliminato.")
    return 0


# --- primer --------------------------------------------------------------------------
def cmd_primer_list(app: App, args: argparse.Namespace) -> int:
    names = app.workspace.list_primers()
    if not names:
        ui.info("Nessun primer nel workspace.")
        ui.hint("Generane uno con: ragpdf primer generate <documento>")
        return 0
    users = {}
    for d in app.documents.list():
        if d.info.primer:
            users.setdefault(d.info.primer, []).append(d.id)
    ui.table(["primer", "usato da"], [[n, ", ".join(users.get(n, [])) or "-"] for n in names])
    return 0


def cmd_primer_generate(app: App, args: argparse.Namespace) -> int:
    doc = app.documents.get(args.doc)
    return 0 if _generate_primer(app, doc, args.name, args.force, args.pages) else 1


def cmd_primer_set(app: App, args: argparse.Namespace) -> int:
    doc = app.documents.get(args.doc)
    source = Path(args.primer)
    if source.suffix.lower() == ".md" and source.exists():
        name = source.stem
        target = app.workspace.primer_path(name)
        if target.resolve() != source.resolve():
            if target.exists() and not ui.ask_yes_no(f"Il primer '{name}' esiste già. Sovrascriverlo?", default=False):
                return 1
            shutil.copyfile(source, target)
            ui.ok(f"Primer copiato in {target}")
    else:
        name = args.primer
    app.assign_primer(doc, name)
    ui.ok(f"Primer '{name}' associato a '{doc.id}'.")
    return 0


# --- specifiche ----------------------------------------------------------------------
def cmd_spec_list(app: App, args: argparse.Namespace) -> int:
    names = app.workspace.list_specs()
    templates = sorted(p.stem for p in SPEC_TEMPLATES_DIR.glob("*.yaml"))
    if names:
        ui.table(["specifica", "descrizione"], [[n, _spec_description(app, n)] for n in names])
    else:
        ui.info("Nessuna specifica nel workspace.")
    ui.info(f"\nModelli disponibili per 'ragpdf spec new <nome> --from <modello>': {', '.join(templates)}")
    return 0


def _spec_description(app: App, name: str) -> str:
    try:
        return app.load_spec(name).description.strip().splitlines()[0][:70]
    except (SpecError, IndexError):
        return "(non valida: ragpdf spec check " + name + ")"


def cmd_spec_new(app: App, args: argparse.Namespace) -> int:
    template = SPEC_TEMPLATES_DIR / f"{args.template}.yaml"
    if not template.exists():
        available = ", ".join(sorted(p.stem for p in SPEC_TEMPLATES_DIR.glob("*.yaml")))
        ui.error(f"Modello '{args.template}' non trovato. Disponibili: {available}")
        return 1
    target = app.workspace.spec_path(args.name)
    if target.exists() and not ui.ask_yes_no(f"La specifica '{args.name}' esiste già. Sovrascriverla?", default=False):
        return 1
    text = template.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("name:"):
            lines[i] = f"name: {args.name}"
            break
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ui.ok(f"Specifica creata: {target}")
    ui.hint("Aprila con un editor di testo e adattala: righe, colonne, istruzioni.")
    ui.hint(f"Poi controllala con: ragpdf spec check {args.name}")
    return 0


def cmd_spec_draft(app: App, args: argparse.Namespace) -> int:
    from ragpdf.indexing.spec_draft import DraftInput, draft_spec

    target = app.workspace.spec_path(args.name)
    if target.exists() and not ui.ask_yes_no(f"La specifica '{args.name}' esiste già. Sovrascriverla?", default=False):
        return 1

    if args.request_file:
        request = Path(args.request_file).read_text(encoding="utf-8")
    elif args.request:
        request = args.request
    else:
        ui.info("Descrivi a parole cosa vuoi nel CSV: cosa rappresenta una riga, quali colonne,")
        ui.info("regole particolari (es. 'se manca il codice OEM lascia vuoto').")
        request = ui.ask_text("Richiesta")
    if not request.strip():
        ui.error("Serve una descrizione di cosa estrarre (--request \"...\" oppure --request-file file.txt).")
        return 1

    doc = app.documents.get(args.doc) if args.doc else None
    primer = ""
    if doc and doc.info.primer:
        primer = app.primer_text(doc)
    pages = None
    if doc and args.pages:
        from ragpdf.core.render import parse_page_ranges

        pages = parse_page_ranges(args.pages, doc.info.page_count)

    inp = DraftInput(
        name=args.name,
        request=request,
        example_csv=Path(args.example_csv) if args.example_csv else None,
        doc=doc,
        primer_text=primer,
        pages=pages,
    )
    ui.info("Chiedo a Gemini una bozza della specifica...")
    try:
        draft_spec(inp, app.client(), target)
    except SpecError as e:
        ui.error(f"La bozza generata non è valida anche dopo una correzione:\n{e}")
        ui.hint(f"Il file è stato salvato comunque, puoi correggerlo a mano: {target}")
        return 1
    ui.ok(f"Bozza della specifica salvata: {target}")
    if inp.pages:
        ui.info(f"Gemini ha visto le pagine {', '.join(map(str, inp.pages))} del documento.")
    ui.hint("Rileggila e correggila, poi provala su poche pagine:")
    ui.hint(f"ragpdf spec check {args.name}")
    if doc:
        ui.hint(f"ragpdf index {doc.id} --spec {args.name} --pages <alcune pagine>")
    return 0


def cmd_spec_check(app: App, args: argparse.Namespace) -> int:
    spec = app.load_spec(args.name)
    ui.ok(f"La specifica '{spec.name}' è valida.")
    ui.info(f"Una riga = {spec.row.strip()}")
    ui.table(
        ["colonna", "tipo", "obbligatoria", "contesto", "controllo"],
        [[c.name, c.type, "sì" if c.required else "", "sì" if c.context else "",
          c.pattern or (", ".join(c.allowed_values) if c.allowed_values else "")] for c in spec.columns],
    )
    extra = [c for c in (spec.output.page_column, spec.output.source_column) if c]
    if extra:
        ui.info(f"Colonne aggiunte automaticamente: {', '.join(extra)}")
    if spec.mappings:
        ui.info("Tabelle di conversione: " + ", ".join(f"{k} ({len(v.values)} voci)" for k, v in spec.mappings.items()))
    if spec.examples.csv:
        ui.info(f"CSV d'esempio: {spec.example_csv_path()} (prime {spec.examples.max_rows} righe)")
    if spec.pages:
        ui.info(f"Pagine elaborate: {spec.pages}")

    if args.show_prompt:
        doc = app.documents.get(args.show_prompt) if args.show_prompt != "-" else None
        primer = app.primer_text(doc) if doc and doc.info.primer else "(primer del documento)"
        total = doc.info.page_count if doc else 100
        ui.title(f"Prompt inviato a Gemini per la pagina {args.page}")
        print(PromptBuilder(spec, primer, total).page_prompt(args.page))
    return 0


# --- indicizzazione ------------------------------------------------------------------
def cmd_index(app: App, args: argparse.Namespace) -> int:
    docs = _resolve_docs(app, args.docs, args.all)
    spec = app.load_spec(_resolve_spec_name(app, args.spec))
    extractor = app.extractor()
    parallelism = app.config.indexing.max_workers or extractor.client.parallelism
    exit_code = 0

    for doc in docs:
        ui.title(f"Indicizzazione di '{doc.id}' con la specifica '{spec.name}'")
        primer = _ensure_primer(app, doc, assume_yes=args.yes)
        if primer is None:
            ui.info("Indicizzazione annullata.")
            exit_code = 1
            continue

        force = args.force
        try:
            manifest = extractor.open_manifest(doc, spec, primer, force=force)
        except SpecChangedError as e:
            ui.warn(str(e))
            ui.info("Continuare mescolerebbe risultati ottenuti con regole diverse.")
            if not ui.ask_yes_no("Rifare l'indicizzazione da capo con le regole attuali?", default=False,
                                 assume_yes=args.yes):
                ui.hint("Per esportare comunque i risultati esistenti usa: ragpdf export")
                exit_code = 1
                continue
            force = True
            manifest = extractor.open_manifest(doc, spec, primer, force=True)

        pages = selected_pages(doc, spec, args.pages)
        todo = manifest.pages_to_process(app.config.indexing.max_page_attempts, only=pages)
        if todo:
            rpm = app.config.gemini.requests_per_minute_per_key * parallelism
            minutes = max(1, round(len(todo) / rpm))
            ui.info(f"Pagine da elaborare: {len(todo)} su {len(pages)} selezionate "
                    f"({len(pages) - len(todo)} già fatte). Richieste parallele: {parallelism}.")
            ui.info(f"Tempo stimato: circa {minutes} minuti (dipende dalle quote API).")
            if not ui.ask_yes_no("Avvio?", default=True, assume_yes=args.yes):
                ui.info("Indicizzazione annullata.")
                continue

        report = extractor.run(doc, spec, primer, pages=args.pages, force=False)
        _print_report(report, "Indicizzazione")
        if report.stopped_reason or report.failed:
            exit_code = 1

        result = app.export(doc, spec)
        _print_export(app, result, doc.id)

    return exit_code


def cmd_export(app: App, args: argparse.Namespace) -> int:
    spec = app.load_spec(_resolve_spec_name(app, args.spec))
    if args.all or not args.docs:
        docs = app.indexed_documents(spec.name)
        if not docs:
            ui.error(f"Nessun documento è stato indicizzato con la specifica '{spec.name}'.")
            return 1
    else:
        docs = [app.documents.get(r) for r in args.docs]

    for doc in docs:
        _print_export(app, app.export(doc, spec), doc.id)

    if args.merge or (args.all and len(docs) > 1):
        merged = app.export_merged(spec, docs)
        ui.ok(f"CSV unico di {len(docs)} documenti ({merged.index.stats['righe_finali']} righe): {merged.csv_path}")
    return 0


def cmd_compare(app: App, args: argparse.Namespace) -> int:
    from ragpdf.core.render import parse_page_ranges
    from ragpdf.indexing.compare import compare_rows, filter_reference
    from ragpdf.indexing.export import _to_csv, _write
    from ragpdf.indexing.spec import read_csv_rows

    spec = app.load_spec(_resolve_spec_name(app, args.spec))
    doc = app.documents.get(args.doc)
    produced = app.export(doc, spec).index
    ref_columns, reference = read_csv_rows(Path(args.reference))

    page_col, source_col = spec.output.page_column, spec.output.source_column
    pages = parse_page_ranges(args.pages, doc.info.page_count) if args.pages else None
    if args.pages is None and not args.all_reference:
        # di default si confrontano solo le pagine effettivamente elaborate
        processed = sorted({int(r[page_col]) for r in produced.rows if page_col and r.get(page_col)})
        pages = processed or None
    reference = filter_reference(reference, source_col, doc.info.source_filename, page_col, pages)

    if args.key:
        keys = [k.strip() for k in args.key.split(",") if k.strip()]
    else:
        excluded = {page_col, source_col}
        keys = [c for c in produced.columns if c in ref_columns and c not in excluded]
    missing_cols = [k for k in keys if k not in ref_columns or k not in produced.columns]
    if not keys or missing_cols:
        ui.error("Colonne chiave non valide: " + (", ".join(missing_cols) or "nessuna colonna in comune"))
        ui.hint(f"Colonne del riferimento: {', '.join(ref_columns)}")
        return 1

    result = compare_rows(produced.rows, reference, keys)
    ui.title(f"Confronto '{doc.id}' con {Path(args.reference).name}")
    ui.info(f"Colonne confrontate: {', '.join(keys)}")
    if pages:
        ui.info(f"Righe del riferimento considerate: {result.reference_rows} (pagine {args.pages or 'elaborate'})")
    ui.table(
        ["", "righe"],
        [["prodotte dal tool", result.produced_rows], ["nel riferimento", result.reference_rows],
         ["trovate in entrambi", result.matched], ["mancanti (solo nel riferimento)", len(result.missing)],
         ["in più (solo nel tool)", len(result.extra)]],
    )
    ui.info(f"Copertura: {result.recall:.1%}   Precisione: {result.precision:.1%}")

    out = app.workspace.exports_dir / spec.name / f"{doc.id}_confronto.csv"
    diff_rows = [{"esito": "mancante", **r} for r in result.missing] + [{"esito": "in più", **r} for r in result.extra]
    _write(out, _to_csv(["esito", *keys], diff_rows, spec.output.delimiter), spec.output.encoding)
    ui.ok(f"Differenze riga per riga: {out}")
    return 0


# --- RAG -----------------------------------------------------------------------------
def cmd_rag_describe(app: App, args: argparse.Namespace) -> int:
    from ragpdf.rag.pipeline import describe_document

    exit_code = 0
    for doc in _resolve_docs(app, args.docs, args.all):
        ui.title(f"Descrizione delle pagine di '{doc.id}'")
        primer = _ensure_primer(app, doc, assume_yes=args.yes)
        if primer is None:
            exit_code = 1
            continue
        report = describe_document(doc, primer, app.client(), app.config, force=args.force)
        _print_report(report, "Descrizione")
        if report.stopped_reason or report.failed:
            exit_code = 1
    ui.hint("Poi aggiorna l'indice di ricerca con: ragpdf rag build")
    return exit_code


def cmd_rag_build(app: App, args: argparse.Namespace) -> int:
    from ragpdf.rag.pipeline import build_index
    from ragpdf.rag.vector_index import VectorIndex

    index = VectorIndex(app.workspace.vector_index_dir, app.config.rag.collection_name)
    n = build_index(app.documents.list(), app.config, index)
    ui.ok(f"Indice aggiornato: {n} pagine ({index.count()} in totale).")
    return 0


def cmd_rag_ask(app: App, args: argparse.Namespace) -> int:
    from ragpdf.rag.pipeline import QueryEngine
    from ragpdf.rag.vector_index import VectorIndex

    index = VectorIndex(app.workspace.vector_index_dir, app.config.rag.collection_name)
    if index.count() == 0:
        ui.error("L'indice di ricerca è vuoto.")
        ui.hint("Prima: ragpdf rag describe <documento>  e poi  ragpdf rag build")
        return 1
    doc_id = app.documents.get(args.doc).id if args.doc else None
    answer = QueryEngine(index, app.client(), app.config).ask(args.question, doc_id)
    ui.title("Risposta")
    print(answer.text)
    if answer.sources:
        ui.info("\nFonti:")
        for s in answer.sources:
            ui.info(f"  - {s['source']}, pagina {s['page']}")
    return 0


def _cmd_setup(app: App, args: argparse.Namespace) -> int:
    from ragpdf.cli_setup import cmd_setup

    return cmd_setup(app, args)


# =====================================================================================
# Parser
# =====================================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ragpdf",
        description="Elaborazione di documenti PDF con Gemini: indicizzazione in CSV e domande (RAG).",
        epilog="Aiuto su un comando: ragpdf <comando> --help",
    )
    parser.add_argument("--version", action="version", version=f"ragpdf {__version__}")
    parser.add_argument("--config", help="file di configurazione alternativo (default: configs/default.yaml)")
    sub = parser.add_subparsers(dest="command", metavar="<comando>")

    p = sub.add_parser("doctor", help="verifica che sia tutto pronto (chiavi, cartelle, documenti)")
    p.add_argument("--online", action="store_true", help="prova anche una richiesta reale a Gemini")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("add", help="aggiunge uno o più PDF al workspace")
    p.add_argument("pdf", nargs="+", help="percorso del file PDF (anche più di uno)")
    p.add_argument("--id", help="identificativo da usare (default: ricavato dal nome del file)")
    p.add_argument("--primer", help="primer da associare (nome di un primer esistente)")
    p.add_argument("--no-primer", action="store_true", help="non chiedere nulla sul primer ora")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser(
        "setup",
        help="CONFIGURAZIONE GUIDATA: primer e specifica costruiti insieme a Gemini, con anteprima",
        description="Gemini guarda alcune pagine scelte da te, propone come indicizzare il documento e "
                    "prova l'estrazione; tu correggi a parole finché il risultato non ti convince.",
    )
    p.add_argument("doc", help="documento del workspace, oppure percorso di un PDF da aggiungere")
    p.add_argument("--pages", help="pagine da mostrare a Gemini, es. '1,4,10-14,102,105'")
    p.add_argument("--goal", help="cosa vuoi ottenere, a parole (altrimenti viene chiesto)")
    p.add_argument("--reference", help="CSV di riferimento (modello di formato e confronto)")
    p.add_argument("--name", help="nome di primer e specifica (default: id del documento)")
    p.add_argument("--primer", help="parti da questo primer esistente (insieme a --spec)")
    p.add_argument("--spec", help="parti da questa specifica esistente (insieme a --primer)")
    p.add_argument("--new", action="store_true", help="ricomincia da capo invece di riprendere la sessione")
    p.set_defaults(func=_cmd_setup)

    p = sub.add_parser("list", help="elenca i documenti e lo stato delle elaborazioni")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="elimina un documento e i suoi risultati")
    p.add_argument("doc")
    p.add_argument("-y", "--yes", action="store_true", help="non chiedere conferma")
    p.set_defaults(func=cmd_remove)

    # primer
    pp = sub.add_parser("primer", help="gestione dei primer (descrizione di com'è fatto un documento)")
    psub = pp.add_subparsers(dest="primer_command", metavar="<azione>", required=True)
    p = psub.add_parser("list", help="elenca i primer")
    p.set_defaults(func=cmd_primer_list)
    p = psub.add_parser("generate", help="genera con Gemini la bozza del primer di un documento")
    p.add_argument("doc")
    p.add_argument("--name", help="nome del primer (default: id del documento)")
    p.add_argument("--force", action="store_true", help="sovrascrivi un primer con lo stesso nome")
    p.add_argument("--pages", help="pagine da mostrare a Gemini, es. '1,4,10-14,102' (default: automatico)")
    p.set_defaults(func=cmd_primer_generate)
    p = psub.add_parser("set", help="associa un primer (nome o file .md) a un documento")
    p.add_argument("doc")
    p.add_argument("primer", help="nome di un primer del workspace, o percorso di un file .md")
    p.set_defaults(func=cmd_primer_set)

    # specifiche
    sp = sub.add_parser("spec", help="gestione delle specifiche di indicizzazione (cosa mettere nel CSV)")
    ssub = sp.add_subparsers(dest="spec_command", metavar="<azione>", required=True)
    p = ssub.add_parser("list", help="elenca le specifiche e i modelli disponibili")
    p.set_defaults(func=cmd_spec_list)
    p = ssub.add_parser("new", help="crea una nuova specifica partendo da un modello")
    p.add_argument("name", help="nome della specifica (es. ricambi)")
    p.add_argument("--from", dest="template", default="base", help="modello da cui partire (default: base)")
    p.set_defaults(func=cmd_spec_new)
    p = ssub.add_parser("draft", help="fa scrivere a Gemini la bozza di una specifica partendo da una descrizione")
    p.add_argument("name", help="nome della specifica (es. ricambi)")
    p.add_argument("--request", help="cosa vuoi nel CSV, a parole (tra virgolette)")
    p.add_argument("--request-file", help="file di testo con la richiesta (per descrizioni lunghe)")
    p.add_argument("--example-csv", help="CSV d'esempio di un indice già fatto (nomi e ordine delle colonne)")
    p.add_argument("--doc", help="documento da mostrare a Gemini (primer e alcune pagine)")
    p.add_argument("--pages", help="pagine del documento da mostrare (default: alcune pagine centrali)")
    p.set_defaults(func=cmd_spec_draft)
    p = ssub.add_parser("check", help="controlla una specifica e mostra cosa verrà estratto")
    p.add_argument("name")
    p.add_argument("--show-prompt", metavar="DOCUMENTO",
                   help="mostra il prompt completo per una pagina del documento ('-' = senza documento)")
    p.add_argument("--page", type=int, default=1, help="pagina per --show-prompt (default 1)")
    p.set_defaults(func=cmd_spec_check)

    # indicizzazione
    p = sub.add_parser("index", help="indicizza uno o più documenti e produce il CSV")
    p.add_argument("docs", nargs="*", help="documenti (id, nome del file o parte dell'id)")
    p.add_argument("--all", action="store_true", help="tutti i documenti del workspace")
    p.add_argument("--spec", help="specifica da usare (se ce n'è una sola si usa quella)")
    p.add_argument("--pages", help="solo alcune pagine, es. '20-30' o '5,8,12-' (utile per una prova)")
    p.add_argument("--force", action="store_true", help="rifai da capo anche le pagine già elaborate")
    p.add_argument("-y", "--yes", action="store_true", help="non chiedere conferme")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("export", help="rigenera i CSV dai risultati salvati (senza chiamare Gemini)")
    p.add_argument("docs", nargs="*", help="documenti (default: tutti quelli indicizzati)")
    p.add_argument("--all", action="store_true", help="tutti i documenti indicizzati, più il CSV unico")
    p.add_argument("--spec", help="specifica")
    p.add_argument("--merge", action="store_true", help="crea anche un CSV unico (TUTTI.csv)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("compare", help="confronta il CSV prodotto con un CSV di riferimento (es. fatto a mano)")
    p.add_argument("doc", help="documento indicizzato")
    p.add_argument("--reference", required=True, help="CSV di riferimento")
    p.add_argument("--spec", help="specifica")
    p.add_argument("--key", help="colonne da confrontare, separate da virgola (default: tutte quelle in comune)")
    p.add_argument("--pages", help="considera solo queste pagine del riferimento (default: quelle elaborate)")
    p.add_argument("--all-reference", action="store_true", help="usa tutto il riferimento, senza filtrare le pagine")
    p.set_defaults(func=cmd_compare)

    # RAG
    rp = sub.add_parser("rag", help="modalità domande e risposte sui documenti")
    rsub = rp.add_subparsers(dest="rag_command", metavar="<azione>", required=True)
    p = rsub.add_parser("describe", help="descrive le pagine dei documenti con Gemini")
    p.add_argument("docs", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_rag_describe)
    p = rsub.add_parser("build", help="aggiorna l'indice di ricerca con le descrizioni (senza Gemini)")
    p.set_defaults(func=cmd_rag_build)
    p = rsub.add_parser("ask", help="fa una domanda sui documenti")
    p.add_argument("question")
    p.add_argument("--doc", help="limita la ricerca a un documento")
    p.set_defaults(func=cmd_rag_ask)

    return parser


def main(argv: list[str] | None = None) -> int:
    ui.setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        from ragpdf.core.config import load_config

        app = App(load_config(args.config))
        return args.func(app, args) or 0
    except (ConfigError, DocumentError, SpecError, GeminiError, PrimerMissingError, FileNotFoundError, ValueError,
            ImportError, SetupError) as e:
        ui.error(str(e))
        return 1
    except KeyboardInterrupt:
        ui.warn("Interrotto dall'utente. Il lavoro fatto finora è salvato.")
        return 130
    except Exception as e:  # errore imprevisto: messaggio breve, dettagli su richiesta
        if os.getenv("RAGPDF_DEBUG"):
            raise
        ui.error(f"Errore imprevisto: {type(e).__name__}: {e}")
        ui.hint("Per vedere i dettagli tecnici rilancia con la variabile d'ambiente RAGPDF_DEBUG=1")
        return 2


if __name__ == "__main__":
    sys.exit(main())
