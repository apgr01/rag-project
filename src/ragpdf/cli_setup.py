"""Interfaccia della sessione guidata: ``ragpdf setup <documento>``.

Qui c'è solo il dialogo con l'utente; la logica è in ``setup_session.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ragpdf import ui
from ragpdf.app import App
from ragpdf.core.documents import Document, DocumentError, DuplicateDocumentError
from ragpdf.core.gemini import GeminiError
from ragpdf.core.render import format_pages, parse_page_ranges
from ragpdf.indexing.spec import SpecError
from ragpdf.setup_session import Preview, SetupError, SetupSession, default_context_pages

HELP = """
Comandi disponibili (oppure scrivi direttamente, a parole, cosa correggere):
  /ok                  salva primer e specifica e termina (poi puoi avviare l'indicizzazione)
  /anteprima PAGINE    rifai la prova su altre pagine, es. /anteprima 40-41,77
  /pagine PAGINE       cambia le pagine che Gemini guarda per capire il documento
                       (valgono dalla prossima correzione), es. /pagine 1-6,40-41,90
  /mostra primer       mostra il primer attuale         (/mostra specifica, /mostra righe)
  /apri csv            apri il CSV di prova (es. in Excel)   (/apri primer, /apri specifica)
  /modifica primer     correggi tu il primer in un editor     (/modifica specifica)
  /note                scrivi una correzione lunga nel Blocco note
  /indietro            torna alla versione precedente
  /riferimento FILE    imposta un CSV di riferimento per il confronto
  /esci                esci senza salvare (la sessione resta: riprendi con ragpdf setup)
  /aiuto               questo elenco
"""

PAGES_EXPLANATION = """Scegli le pagine che servono a CAPIRE il documento (non serve mandarlo tutto):
  - indice o elenco delle sezioni;
  - legende, "come leggere il catalogo", tabelle di sigle;
  - 2-3 pagine tipo per ogni layout diverso (es. le due pagine di una scheda prodotto).
Formato: 1,4,10-14,102,105   (massimo 40 pagine)"""


def _clean_path(text: str) -> str:
    # "Copia come percorso" di Windows aggiunge le virgolette.
    return text.strip().strip('"').strip("'").strip()


def _ask_pages(question: str, default: list[int], total: int) -> list[int]:
    while True:
        answer = ui.ask_text(question, format_pages(default))
        try:
            pages = parse_page_ranges(answer, total)
        except ValueError as e:
            ui.warn(str(e))
            continue
        if pages:
            return pages
        ui.warn("Indica almeno una pagina.")


def _ask_feedback_in_editor(session: SetupSession) -> str:
    draft = session.folder / "nota.txt"
    draft.write_text(
        "# Scrivi qui cosa correggere, salva e chiudi il file.\n"
        "# Le righe che iniziano con # vengono ignorate.\n\n",
        encoding="utf-8",
    )
    if not ui.edit_file(draft):
        return ui.ask_multiline("Editor non disponibile: scrivi qui la correzione")
    lines = [line for line in draft.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip()


def _resolve_document(app: App, ref: str) -> Document:
    path = Path(_clean_path(ref))
    if path.suffix.lower() == ".pdf" and path.is_file():
        try:
            doc = app.documents.add(path)
            ui.ok(f"Documento aggiunto come '{doc.id}' ({doc.info.page_count} pagine).")
            from ragpdf.cli import _warn_if_scanned

            _warn_if_scanned(doc)
            return doc
        except DuplicateDocumentError as e:
            ui.info(f"Il PDF è già nel workspace: uso '{e.existing.id}'.")
            return app.documents.get(e.existing.id)
    return app.documents.get(ref)


# --------------------------------------------------------------------------- visualizzazione
def _show_round(session: SetupSession) -> None:
    notes = session.round_notes()
    ui.title(f"Versione {session.state.current_round}")
    if notes.get("summary"):
        print(notes["summary"].strip())
    try:
        spec = session.load_spec()
    except SpecError as e:
        ui.warn(f"La specifica attuale non è valida: {e}")
        return
    ui.info(f"\nUna riga del CSV = {spec.row.strip()}")
    ui.compact_table(
        ["colonna", "descrizione"],
        [[c.name + (" *" if c.required else "") + (" (intestazione)" if c.context else ""),
          c.description.strip().replace("\n", " ")] for c in spec.columns],
        max_cell=80,
    )
    ui.info("  * = obbligatoria;  (intestazione) = dato di pagina, ereditato se la pagina non lo ripete")
    primer_lines = len(session.primer_text().splitlines())
    ui.info(f"Primer: {primer_lines} righe (leggilo con /mostra primer o /apri primer)")
    if spec.pages:
        ui.info(f"Pagine che verranno indicizzate: {spec.pages}")
    questions = notes.get("questions") or []
    if questions:
        ui.info("\nDomande di Gemini (rispondi pure nella prossima correzione):")
        for q in questions:
            ui.info(f"  ? {q}")


def _show_preview(session: SetupSession, preview: Preview) -> None:
    pages = sorted(preview.rows_per_page)
    ui.title(f"Prova di estrazione sulle pagine {format_pages(pages)}")
    ui.info("Righe per pagina: " + ", ".join(f"p.{p}: {n}" for p, n in sorted(preview.rows_per_page.items())))
    if preview.report.failed:
        for p, msg in preview.report.failed.items():
            ui.warn(f"Pagina {p} fallita: {msg}")
    if preview.report.stopped_reason:
        ui.warn(preview.report.stopped_reason)

    rows = preview.index.rows
    if rows:
        spec = session.load_spec()
        hidden = {spec.output.source_column}
        cols = [c for c in preview.index.columns if c not in hidden]
        ui.compact_table(cols, [[r.get(c, "") for c in cols] for r in rows[:15]])
        if len(rows) > 15:
            ui.info(f"  ... altre {len(rows) - 15} righe (/apri csv per vederle tutte)")
    else:
        ui.warn("Nessuna riga estratta da queste pagine.")

    s = preview.index.stats
    if s.get("errori") or s.get("avvisi"):
        ui.warn(f"Controlli: {s.get('errori', 0)} errori, {s.get('avvisi', 0)} avvisi "
                f"(dettagli in {preview.anomalies_path.name if preview.anomalies_path else '-'})")
    if preview.comparison is not None:
        c = preview.comparison
        ui.info(f"Confronto con il riferimento: copertura {c.recall:.0%}, precisione {c.precision:.0%} "
                f"({c.matched} righe uguali, {len(c.missing)} mancanti, {len(c.extra)} in più)")
        for r in c.missing[:5]:
            ui.info("  manca:  " + " | ".join(r.values()))
        for r in c.extra[:5]:
            ui.info("  in più: " + " | ".join(r.values()))
    elif preview.comparison_note:
        ui.info(preview.comparison_note)
    ui.info(f"CSV di prova: {preview.csv_path}")


def _preview(session: SetupSession) -> None:
    ui.info(f"\nProvo l'estrazione sulle pagine {format_pages(session.state.preview_pages)}...")
    preview = session.run_preview(show_progress=True)
    _show_preview(session, preview)


def _show_saved_preview(session: SetupSession) -> bool:
    """Mostra l'ultima anteprima salvata (senza nuove richieste). False se non c'è."""
    info = session.round_notes().get("preview")
    if not info or not session.preview_csv_path.exists():
        return False
    ui.title(f"Ultima prova salvata (pagine {format_pages(info['pages'])})")
    ui.info("Righe per pagina: " + ", ".join(f"p.{p}: {n}" for p, n in info["rows_per_page"].items()))
    from ragpdf.indexing.spec import read_csv_rows

    columns, rows = read_csv_rows(session.preview_csv_path)
    hidden = {session.load_spec().output.source_column}
    cols = [c for c in columns if c not in hidden]
    if rows:
        ui.compact_table(cols, [[r.get(c, "") for c in cols] for r in rows[:15]])
    if info.get("comparison"):
        c = info["comparison"]
        ui.info(f"Confronto con il riferimento: copertura {c['recall']:.0%}, precisione {c['precision']:.0%}")
    ui.info(f"CSV di prova: {session.preview_csv_path}   (/anteprima per rifarla)")
    return True


# --------------------------------------------------------------------------- avvio
def _start_new(app: App, doc: Document, args: argparse.Namespace) -> SetupSession | None:
    ui.title(f"Configurazione guidata di '{doc.id}' ({doc.info.page_count} pagine)")
    ui.info("Gemini guarderà alcune pagine del documento e proporrà come indicizzarlo.")
    ui.info("Poi proverà l'estrazione su poche pagine e tu potrai correggere a parole,")
    ui.info("finché il risultato non ti convince.\n")

    start_from: tuple[str, str] | None = None
    if args.primer or args.spec:
        if not (args.primer and args.spec):
            raise SetupError("Per partire da file esistenti indica sia --primer sia --spec.")
        start_from = (args.primer, args.spec)
    elif app.workspace.list_primers() and app.workspace.list_specs():
        choice = ui.choose(
            "Da dove vuoi partire?",
            [("new", "Da zero: Gemini analizza questo documento"),
             ("existing", "Da primer e specifica esistenti (documento dello stesso tipo/editore)")],
            default="new",
        )
        if choice == "existing":
            primer = ui.choose("Quale primer?", [(n, n) for n in app.workspace.list_primers()])
            spec = ui.choose("Quale specifica?", [(n, n) for n in app.workspace.list_specs()])
            if primer and spec:
                start_from = (primer, spec)

    if args.pages:
        context = parse_page_ranges(args.pages, doc.info.page_count) or []
    elif start_from:
        context = default_context_pages(doc.info.page_count)
    else:
        ui.info(PAGES_EXPLANATION)
        context = _ask_pages("Pagine da mostrare a Gemini", default_context_pages(doc.info.page_count),
                             doc.info.page_count)

    goal = args.goal or ""
    reference = _clean_path(args.reference) if args.reference else ""
    if not start_from:
        if not goal:
            ui.info("\nCosa vuoi ottenere? Descrivi il CSV a parole: cosa è una riga, quali colonne, regole")
            ui.info("particolari. Premi invio per lasciare che sia Gemini a proporre, oppure scrivi /note")
            ui.info("per usare il Blocco note.")
            goal = ui.ask_text("Obiettivo")
        if not reference:
            ui.info("\nHai un CSV già fatto (anche di altri cataloghi) da usare come modello di formato e,")
            ui.info("se è dello stesso catalogo, per misurare il risultato? Percorso del file, oppure invio.")
            while True:
                reference = _clean_path(ui.ask_text("CSV di riferimento"))
                if not reference or Path(reference).is_file():
                    break
                ui.warn(f"File non trovato: {reference}")

    # Partendo da file esistenti si aggiornano quelli (un solo primer per tipo di catalogo),
    # salvo un nome diverso indicato con --name.
    session = SetupSession.new(
        app, doc, context, goal="", reference_csv=Path(reference) if reference else None,
        primer_name=args.name or (start_from[0] if start_from else None),
        spec_name=args.name or (start_from[1] if start_from else None),
    )
    if goal.strip() == "/note":
        goal = _ask_feedback_in_editor(session)
    session.state.goal = goal.strip()
    session.save_state()

    if start_from:
        session.start_from_existing(*start_from)
        ui.ok(f"Partenza da primer '{start_from[0]}' e specifica '{start_from[1]}'.")
    else:
        ui.info(f"\nGemini sta analizzando {len(context)} pagine ({format_pages(context)}): "
                "di solito serve un minuto o due...")
        session.start()
    return session


def _resume(app: App, doc: Document, args: argparse.Namespace) -> SetupSession | None:
    existing = SetupSession.load(app, doc)
    if existing is None or args.new:
        return None
    if existing.state.current_round == 0:
        return None
    status = "salvata" if existing.state.saved_at else "non salvata"
    ui.info(f"Esiste una sessione guidata per '{doc.id}' (versione {existing.state.current_round}, {status}).")
    if ui.ask_yes_no("Vuoi riprenderla?", default=True):
        return existing
    return None


# --------------------------------------------------------------------------- comandi
def _save(app: App, session: SetupSession) -> bool:
    existing = session.files_that_would_change()
    while existing and not session.state.saved_at:
        names = ", ".join(p.name for p in existing)
        if ui.ask_yes_no(f"Esistono già {names} nel workspace. Sovrascriverli?", default=False):
            break
        new_name = ui.ask_text("Nome da usare per primer e specifica", f"{session.state.spec_name}-2")
        from ragpdf.core.workspace import slugify

        session.state.primer_name = session.state.spec_name = slugify(new_name)
        session.save_state()
        existing = session.files_that_would_change()
    primer_path, spec_path = session.save()
    ui.ok(f"Primer salvato: {primer_path}")
    ui.ok(f"Specifica salvata: {spec_path} (predefinita per '{session.doc.id}')")
    reused = session.reuse_preview()
    if reused:
        ui.info(f"Le {reused} pagine dell'ultima prova sono già contate come indicizzate (nessuna nuova richiesta).")
    return True


def _offer_indexing(app: App, session: SetupSession) -> int:
    from ragpdf import cli

    spec = session.load_spec()
    ui.info(f"\nPer indicizzare tutto il documento: ragpdf index {session.doc.id}")
    if spec.pages:
        ui.info(f"(verranno elaborate le pagine {spec.pages}, come indicato nella specifica)")
    if not ui.ask_yes_no("Avviare ora l'indicizzazione completa?", default=False):
        return 0
    ns = argparse.Namespace(docs=[session.doc.id], all=False, spec=session.state.spec_name,
                            pages=None, force=False, yes=False)
    return cli.cmd_index(app, ns)


def _loop(app: App, session: SetupSession, resumed: bool = False) -> int:
    _show_round(session)
    if not (resumed and _show_saved_preview(session)):
        _preview(session)
    ui.info("\nScrivi a parole cosa correggere (es. 'la colonna MOTORE deve contenere solo il modello'),")
    ui.info("oppure /ok per salvare, /aiuto per gli altri comandi.")

    while True:
        try:
            answer = input("\nCorrezione> ").strip()
        except EOFError:
            answer = "/esci"
        if not answer:
            continue
        command, _, rest = answer.partition(" ")
        command = command.lower()
        rest = rest.strip()

        try:
            if command in ("/ok", "/salva"):
                _save(app, session)
                _print_session_usage(app)
                return _offer_indexing(app, session)
            if command in ("/esci", "/quit"):
                _print_session_usage(app)
                ui.info(f"Sessione conservata: riprendila con  ragpdf setup {session.doc.id}")
                return 0
            if command in ("/aiuto", "/help", "/?"):
                print(HELP)
                continue
            if command == "/anteprima":
                if rest:
                    session.set_preview_pages(parse_page_ranges(rest, session.doc.info.page_count) or [])
                _preview(session)
                continue
            if command == "/pagine":
                if not rest:
                    ui.info(f"Pagine attuali: {format_pages(session.state.context_pages)}")
                    continue
                session.set_context_pages(parse_page_ranges(rest, session.doc.info.page_count) or [])
                ui.ok(f"Gemini guarderà le pagine {format_pages(session.state.context_pages)} "
                      "dalla prossima correzione.")
                continue
            if command == "/mostra":
                what = rest.lower()
                if what.startswith("prim"):
                    print(session.primer_text())
                elif what.startswith("spec"):
                    print(session.spec_text())
                else:
                    _preview_rows = session.preview_csv_path
                    print(_preview_rows.read_text(encoding="utf-8-sig") if _preview_rows.exists() else "(nessuna)")
                continue
            if command == "/apri":
                what = rest.lower()
                target = (session.primer_path if what.startswith("prim")
                          else session.spec_path if what.startswith("spec") else session.preview_csv_path)
                if not ui.open_file(target):
                    ui.info(f"Apri il file: {target}")
                continue
            if command == "/modifica":
                _manual_edit(session, rest.lower())
                continue
            if command == "/indietro":
                session.go_back()
                _show_round(session)
                _preview(session)
                continue
            if command == "/riferimento":
                path = Path(_clean_path(rest))
                if not path.is_file():
                    ui.warn(f"File non trovato: {path}")
                    continue
                session.state.reference_csv = str(path.resolve())
                session.save_state()
                ui.ok("CSV di riferimento impostato: verrà usato dalla prossima correzione o anteprima.")
                continue
            if command == "/note":
                answer = _ask_feedback_in_editor(session)
                if not answer:
                    ui.info("Nessuna correzione scritta.")
                    continue
            elif command.startswith("/"):
                ui.warn(f"Comando sconosciuto: {command}. Scrivi /aiuto per l'elenco.")
                continue

            ui.info("Gemini sta applicando le correzioni...")
            session.revise(answer)
            _show_round(session)
            _preview(session)
        except (SetupError, SpecError, ValueError) as e:
            ui.warn(str(e))
        except GeminiError as e:
            ui.error(str(e))
            ui.hint("La versione attuale non è cambiata. Puoi riprovare o uscire con /esci.")


def _print_session_usage(app: App) -> None:
    usage = getattr(app._client, "usage", None)
    if usage is not None and usage.requests:
        ui.info(f"Consumo della sessione: {usage.describe()}.")


def _manual_edit(session: SetupSession, what: str) -> None:
    import shutil
    import tempfile

    is_primer = what.startswith("prim")
    if not (is_primer or what.startswith("spec")):
        ui.warn("Indica cosa modificare: /modifica primer oppure /modifica specifica")
        return
    source = session.primer_path if is_primer else session.spec_path
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / source.name
        shutil.copyfile(source, copy)
        if not ui.edit_file(copy):
            ui.info(f"Nessun editor disponibile. Modifica direttamente {source} e poi scrivi /anteprima.")
            return
        text = copy.read_text(encoding="utf-8")
    if text == source.read_text(encoding="utf-8"):
        ui.info("Nessuna modifica.")
        return
    try:
        if is_primer:
            session.manual_edit_done(primer_text=text)
        else:
            session.manual_edit_done(spec_text=text)
    except SpecError as e:
        ui.warn(f"La specifica modificata non è valida, modifica annullata:\n{e}")
        return
    ui.ok("Modifica registrata come nuova versione.")
    _preview(session)


def cmd_setup(app: App, args: argparse.Namespace) -> int:
    if not ui.interactive():
        ui.error("La configurazione guidata richiede un terminale interattivo.")
        ui.hint("In alternativa: ragpdf primer generate <doc> --pages ...  e  ragpdf spec draft ...")
        return 1
    try:
        doc = _resolve_document(app, args.doc)
    except DocumentError as e:
        ui.error(str(e))
        return 1

    resumed = _resume(app, doc, args)
    session = resumed or _start_new(app, doc, args)
    if session is None:
        return 1
    try:
        return _loop(app, session, resumed=resumed is not None)
    except KeyboardInterrupt:
        ui.warn(f"Interrotto. La sessione è conservata: riprendila con  ragpdf setup {doc.id}")
        return 130
