"""Sessione guidata (progetti con uno o più PDF) con Gemini simulato."""

import re

import pytest
import yaml

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.indexing.spec import SpecError, load_spec
from ragpdf.setup_session import (
    SetupError,
    SetupSession,
    default_context_refs,
    format_page_refs,
    parse_page_refs,
)

SPEC_V1 = """
row: Una riga per ogni codice.
columns:
  - name: MOTORE
    context: true
  - name: CODICE
    required: true
output:
  page_column: Pagina
  source_column: File
"""

SPEC_V2 = SPEC_V1.replace("  - name: CODICE\n", "  - name: CODICE\n    description: codice in maiuscolo\n")


class FakeCatalog:
    """Gemini simulato: risponde alle richieste di setup e di estrazione."""

    def __init__(self):
        self.setup_answers = []      # risposte successive per le richieste di setup
        self.setup_prompts = []

    def __call__(self, req):
        props = (req.json_schema or {}).get("properties", {})
        if "primer_markdown" in props:
            self.setup_prompts.append(req)
            answer = self.setup_answers.pop(0)
            return answer(req) if callable(answer) else answer
        n = int(re.search(r"pagina (\d+) di", req.prompt).group(1))
        return {
            "page_summary": "",
            "page_context": {"motore": f"M{n}"},
            "rows": [{"motore": "", "codice": f"C{n}"}],
            "notes": "",
        }


def answer(spec=SPEC_V1, primer="# Primer\nUn catalogo.", pages=(1, 2), summary="Propongo una riga per codice.",
           notes=()):
    """Risposta di Gemini; ``pages`` sono numeri di pagina dell'ESTRATTO."""
    return {"primer_markdown": primer, "spec_yaml": spec, "summary": summary,
            "questions": ["Serve la colonna versione?"], "preview_pages": list(pages),
            "document_notes": [{"document": d, "notes": t} for d, t in notes]}


def refs(doc, *pages):
    return [(doc.id, p) for p in pages]


@pytest.fixture
def env(config, pdf_factory):
    fake = FakeCatalog()
    app = App(config, client=FakeGeminiClient(fake, parallelism=2))
    doc = app.add_document(pdf_factory("Catalogo Test.pdf", pages=8))
    project = app.projects.get(doc.id)
    return app, doc, project, fake


@pytest.fixture
def multi(config, pdf_factory):
    """Progetto con due PDF: 'alfa' (8 pagine) e 'beta' (6 pagine)."""
    fake = FakeCatalog()
    app = App(config, client=FakeGeminiClient(fake, parallelism=2))
    project = app.projects.create("Motori")
    alfa = app.add_document(pdf_factory("Alfa.pdf", pages=8, prefix="A"), project=project)
    beta = app.add_document(pdf_factory("Beta.pdf", pages=6, prefix="B"), project=project)
    return app, project, alfa, beta, fake


# ------------------------------------------------------------------ pagine
def test_page_refs_single_and_multi(env, multi):
    _, doc, _, _ = env
    assert parse_page_refs("1-3, 5", [doc]) == refs(doc, 1, 2, 3, 5)
    app, project, alfa, beta, _ = multi
    docs = [alfa, beta]
    parsed = parse_page_refs("alfa: 1-2; Beta: 6", docs)
    assert parsed == [("alfa", 1), ("alfa", 2), ("beta", 6)]
    assert format_page_refs(parsed, docs) == "Alfa: 1-2; Beta: 6"
    with pytest.raises(ValueError, match="a quale"):
        parse_page_refs("1-2", docs)
    with pytest.raises(ValueError, match="non presente"):
        parse_page_refs("gamma: 1", docs)
    with pytest.raises(ValueError, match="fuori"):
        parse_page_refs("beta: 7", docs)


def test_default_context_refs(env, multi):
    _, doc, _, _ = env
    assert default_context_refs([doc]) == refs(doc, *range(1, 9))
    _, _, alfa, beta, _ = multi
    proposal = default_context_refs([alfa, beta])
    assert ("beta", 3) in proposal and ("beta", 4) in proposal and proposal[0] == ("alfa", 1)


# ------------------------------------------------------------------ un documento
def test_start_sends_subset_with_mapping(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(pages=(3,))]
    s = SetupSession.new(app, project, refs(doc, 1, 5, 7), goal="voglio i codici")
    assert s.start() == 1
    req = fake.setup_prompts[0]
    assert req.pdf_pages == 3
    assert "pagina 2 dell'estratto = pagina 5 del documento originale" in req.prompt
    assert "voglio i codici" in req.prompt and "DOCUMENTI DEL PROGETTO" not in req.prompt
    assert s.state.preview_pages == [(doc.id, 7)]          # pagina 3 dell'estratto
    spec = s.load_spec()
    assert spec.name == doc.id and [c.name for c in spec.columns] == ["MOTORE", "CODICE"]
    assert s.round_notes()["questions"] == ["Serve la colonna versione?"]


def test_without_goal_gemini_is_asked_to_propose(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer()]
    SetupSession.new(app, project, refs(doc, 1, 2)).start()
    assert "proponi tu" in fake.setup_prompts[0].prompt


def test_invalid_spec_is_retried_once(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(spec="columns: [non: valido"), answer()]
    s = SetupSession.new(app, project, refs(doc, 1, 2))
    assert s.start() == 1
    assert "NON ERA VALIDA" in fake.setup_prompts[1].prompt


def test_twice_invalid_spec_keeps_previous_version(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(), answer(spec="row: x"), answer(spec="row: x")]
    s = SetupSession.new(app, project, refs(doc, 1, 2))
    s.start()
    with pytest.raises(SetupError, match="due volte"):
        s.revise("aggiungi una colonna")
    assert s.state.current_round == 1 and s.state.last_round == 1
    assert not s.round_dir(2).exists()


def test_preview_and_revision_loop(env):
    app, doc, project, fake = env
    fake.setup_answers = [
        answer(pages=(2, 99)),                                   # 99 non esiste nell'estratto
        answer(spec=SPEC_V2, pages=(3,), summary="Ho aggiunto la descrizione."),
    ]
    s = SetupSession.new(app, project, refs(doc, 1, 3, 5))
    s.start()
    assert s.state.preview_pages == [(doc.id, 3)]
    preview = s.run_preview(show_progress=False)
    assert [r["CODICE"] for r in preview.index.rows] == ["C3"]
    assert preview.rows_per_page == {(doc.id, 3): 1}
    assert s.preview_csv_path.exists()

    assert s.revise("il codice deve essere maiuscolo") == 2
    prompt = fake.setup_prompts[1].prompt
    assert "il codice deve essere maiuscolo" in prompt
    assert "C3" in prompt and "pagina 3: 1 righe" in prompt   # risultati della prova inviati
    assert s.load_spec().columns[1].description == "codice in maiuscolo"
    assert s.state.preview_pages == [(doc.id, 5)]

    fake.setup_answers = [answer(spec=SPEC_V2, pages=())]
    s.revise("altra correzione")
    assert "- il codice deve essere maiuscolo" in fake.setup_prompts[2].prompt   # osservazioni precedenti
    assert s.state.preview_pages == [(doc.id, 5)]                               # lista vuota: stesse pagine


def test_go_back_and_manual_edit(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(), answer(spec=SPEC_V2)]
    s = SetupSession.new(app, project, refs(doc, 1, 2))
    s.start()
    s.revise("modifica")
    assert s.go_back() == 1
    assert "codice in maiuscolo" not in s.spec_text()
    with pytest.raises(SetupError):
        s.go_back()

    with pytest.raises(SpecError):
        s.manual_edit_done(spec_text="row: x\ncolumns: []\n")
    assert s.state.last_round == 2                     # nessun giro creato
    assert s.manual_edit_done(primer_text="# Primer corretto a mano") == 3
    assert s.primer_text().startswith("# Primer corretto a mano")
    assert s.load_spec().name == doc.id
    assert s.go_to(2) == 2 and [r["round"] for r in s.rounds()] == [1, 2, 3]


def test_comparison_with_reference(env, tmp_path):
    app, doc, project, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text(
        "MOTORE,CODICE,Pagina,File\n"
        "M3,C3,3,Catalogo Test.pdf\n"
        "M3,C3-BIS,3,Catalogo Test.pdf\n"
        "M9,C9,9,Catalogo Test.pdf\n"
        "X,Y,3,Altro.pdf\n",
        encoding="utf-8",
    )
    fake.setup_answers = [answer(pages=(2,))]
    s = SetupSession.new(app, project, refs(doc, 1, 3), reference_csv=ref)
    s.start()
    assert "M3,C3,3" in fake.setup_prompts[0].prompt          # usato come esempio di formato
    assert s.load_spec().example_csv_path() == ref.resolve()
    preview = s.run_preview(show_progress=False)
    c = preview.comparison
    assert (c.reference_rows, c.matched, len(c.missing), len(c.extra)) == (2, 1, 1, 0)
    assert c.missing == [{"MOTORE": "M3", "CODICE": "C3-BIS"}]


def test_comparison_impossible_without_page_column(env, tmp_path):
    app, doc, project, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE\nM3,C3\n", encoding="utf-8")
    fake.setup_answers = [answer(pages=(2,))]
    s = SetupSession.new(app, project, refs(doc, 1, 3), reference_csv=ref)
    s.start()
    preview = s.run_preview(show_progress=False)
    assert preview.comparison is None and "non possibile" in preview.comparison_note


def test_save_writes_workspace_files(env, tmp_path):
    app, doc, project, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE,Pagina,File\nM,C,1,x.pdf\n", encoding="utf-8")
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, project, refs(doc, 1, 2), reference_csv=ref, primer_name="tipo-a", spec_name="tipo-a")
    s.start()
    primer_path, spec_path = s.save()
    assert primer_path == app.workspace.primer_path("tipo-a") and primer_path.exists()
    spec = load_spec(spec_path)
    assert spec.name == "tipo-a"
    assert spec.example_csv_path() == spec_path.with_name("tipo-a_esempio.csv")
    assert spec.example_csv_path().exists()
    assert app.documents.get(doc.id).info.primer == "tipo-a"
    assert app.projects.get(project.id).info.spec == "tipo-a"
    assert s.state.saved_at

    # la specifica salvata funziona con l'indicizzazione vera
    fresh = app.documents.get(doc.id)
    report = app.extractor().run(fresh, spec, app.primer_text(fresh), pages="2-3", show_progress=False)
    assert report.succeeded == 2


def test_resume_and_start_from_existing(env, pdf_factory):
    app, doc, project, fake = env
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, project, refs(doc, 1, 2))
    s.start()
    s.save()
    resumed = SetupSession.load(app, app.projects.get(project.id))
    assert resumed.state.current_round == 1 and resumed.state.saved_at

    other = app.add_document(pdf_factory("Secondo catalogo.pdf", pages=4, prefix="B"))
    other_project = app.projects.get(other.id)
    s2 = SetupSession.new(app, other_project, refs(other, 1, 2))
    s2.start_from_existing(doc.id, doc.id)
    assert len(fake.setup_prompts) == 1            # nessuna chiamata a Gemini
    assert s2.load_spec().name == other.id
    assert s2.state.preview_pages                   # una pagina di prova di ripiego
    assert s2.run_preview(show_progress=False).index.rows


def test_new_session_validation(env, tmp_path):
    app, doc, project, fake = env
    with pytest.raises(SetupError, match="almeno una"):
        SetupSession.new(app, project, [])
    with pytest.raises(SetupError, match="massimo"):
        SetupSession.new(app, project, refs(doc, *range(1, 9)) * 6)
    with pytest.raises(SetupError, match="non trovato"):
        SetupSession.new(app, project, refs(doc, 1), reference_csv=tmp_path / "manca.csv")


def test_spec_from_gemini_with_code_fences_and_wrong_name(env):
    app, doc, project, fake = env
    fenced = "```yaml\nname: altro\n" + SPEC_V1.strip() + "\n```"
    fake.setup_answers = [answer(spec=fenced)]
    s = SetupSession.new(app, project, refs(doc, 1))
    s.start()
    assert yaml.safe_load(s.spec_text())["name"] == doc.id


def test_invented_example_csv_is_dropped(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(spec=SPEC_V1 + "examples:\n  csv: esempio.csv\n")]
    s = SetupSession.new(app, project, refs(doc, 1, 2))
    s.start()
    assert s.load_spec().example_csv_path() is None
    s.run_preview(show_progress=False)                  # nessun errore per file mancante


def test_pages_outside_document_trigger_correction(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(spec=SPEC_V1 + "pages: '5-300'\n"), answer(spec=SPEC_V1 + "pages: '5-'\n")]
    s = SetupSession.new(app, project, refs(doc, 1, 2))
    s.start()
    assert "fuori dal documento" in fake.setup_prompts[1].prompt
    assert s.load_spec().pages == "5-"


def test_existing_spec_with_relative_example_csv(env, tmp_path, pdf_factory):
    app, doc, project, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE,Pagina,File\nM,C,1,x.pdf\n", encoding="utf-8")
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, project, refs(doc, 1, 2), reference_csv=ref)
    s.start()
    s.save()                                            # salva examples.csv: <nome>_esempio.csv
    other = app.add_document(pdf_factory("Gemello.pdf", pages=4, prefix="G"))
    s2 = SetupSession.new(app, app.projects.get(other.id), refs(other, 1, 2))
    s2.start_from_existing(doc.id, doc.id)
    assert s2.load_spec().example_csv_path() == app.workspace.spec_path(doc.id).with_name(f"{doc.id}_esempio.csv")
    s2.run_preview(show_progress=False)
    _, spec_path = s2.save()
    assert load_spec(spec_path).examples.csv == f"{doc.id}_esempio.csv"


def test_save_reuses_preview_only_if_rules_unchanged(env, pdf_factory):
    app, doc, project, fake = env
    fake.setup_answers = [answer(pages=(2, 3))]
    s = SetupSession.new(app, project, refs(doc, 1, 3, 4))
    s.start()
    s.run_preview(show_progress=False)
    s.save()
    assert app.documents.get(doc.id).info.spec == doc.id
    assert s.reuse_preview() == 2
    assert (doc.extraction_dir(doc.id) / "manifest.json").exists()

    other_doc = app.add_document(pdf_factory("altro.pdf", pages=5, prefix="Z"))
    other = SetupSession.new(app, app.projects.get(other_doc.id), refs(other_doc, 1, 2))
    fake.setup_answers = [answer(pages=(2,))]
    other.start()
    other.run_preview(show_progress=False)
    other.save()
    spec_file = app.workspace.spec_path(other.state.spec_name)
    spec_file.write_text(spec_file.read_text(encoding="utf-8") + "instructions: nuova regola\n", encoding="utf-8")
    assert other.reuse_preview() == 0


def test_manual_edit_keeps_preview_pages(env):
    app, doc, project, fake = env
    fake.setup_answers = [answer(pages=(2,))]
    s = SetupSession.new(app, project, refs(doc, 5, 6, 7))
    s.start()
    s.set_preview_pages(refs(doc, 1))
    s.manual_edit_done(primer_text="# P")
    assert s.state.preview_pages == refs(doc, 1)


# ------------------------------------------------------------------ più documenti
def test_multi_document_session(multi, tmp_path):
    app, project, alfa, beta, fake = multi
    fake.setup_answers = [answer(pages=(1,), notes=[("[beta]", "Le schede di Beta hanno il motore in basso.")])]
    s = SetupSession.new(app, project, [("alfa", 1), ("alfa", 4), ("beta", 3)])
    s.start()
    req = fake.setup_prompts[0]
    assert req.pdf_pages == 3
    assert "pagina 3 dell'estratto = Beta [beta], pagina 3" in req.prompt
    assert "DOCUMENTI DEL PROGETTO" in req.prompt and "[alfa] Alfa" in req.prompt
    assert s.document_notes() == {"beta": "Le schede di Beta hanno il motore in basso."}
    # Gemini ha proposto solo una pagina di Alfa: se ne aggiunge una di Beta
    assert s.state.preview_pages == [("alfa", 1), ("beta", 3)]

    preview = s.run_preview(show_progress=False)
    files = {r["File"] for r in preview.index.rows}
    assert files == {"Alfa.pdf", "Beta.pdf"}
    assert preview.rows_per_page == {("alfa", 1): 1, ("beta", 3): 1}
    # la nota di Beta arriva solo nelle richieste delle pagine di Beta
    extraction = [r for r in app.client().requests if r.kind == "json" and "rows" in r.json_schema["properties"]]
    beta_prompts = [r.prompt for r in extraction if "Le schede di Beta" in r.prompt]
    assert len(beta_prompts) == 1 and "NOTE SPECIFICHE PER QUESTO PDF (Beta)" in beta_prompts[0]

    # revisione: le note attuali sono nel prompt; Gemini aggiunge quella di Alfa e toglie
    # esplicitamente quella di Beta (una nota non citata resterebbe invariata)
    fake.setup_answers = [answer(notes=[("Alfa", "In Alfa ignora le pagine pari."), ("[beta]", "")])]
    s.revise("la nota di Beta non serve più; in Alfa ignora le pagine pari")
    assert "[beta] Beta: Le schede di Beta" in fake.setup_prompts[1].prompt
    assert s.document_notes() == {"alfa": "In Alfa ignora le pagine pari."}

    # salvataggio: note nel progetto, regole assegnate a tutti i documenti
    s.run_preview(show_progress=False)
    s.save()
    saved = app.projects.get(project.id)
    assert saved.settings_for("alfa").notes == "In Alfa ignora le pagine pari." and not saved.settings_for("beta").notes
    assert all(app.documents.get(d).info.spec == project.id for d in ("alfa", "beta"))
    assert s.reuse_preview() == 3        # alfa 1 e 4 proposte da Gemini, più beta 3 aggiunta


def test_manual_document_notes(multi):
    app, project, alfa, beta, fake = multi
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, project, [("alfa", 1), ("beta", 1)])
    s.start()
    s.manual_edit_done(document_notes={"beta": "nota a mano"})
    assert s.document_notes() == {"beta": "nota a mano"}
    assert s.round_notes()["origin"] == "manuale"


def test_preview_limit_grows_with_documents(multi):
    app, project, alfa, beta, fake = multi
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, project, [("alfa", 1), ("beta", 1)])
    s.start()
    with pytest.raises(SetupError, match="Al massimo"):
        s.set_preview_pages([("alfa", p) for p in range(1, 8)])
    s.set_preview_pages([("beta", 2)])
    assert s.state.preview_pages == [("beta", 2)]


def test_old_single_document_session_is_converted(env):
    """Sessioni salvate prima dei progetti: pagine come numeri e campo doc_id."""
    from ragpdf.core.fileio import write_json

    app, doc, project, fake = env
    write_json(SetupSession.folder_for(project) / "session.json", {
        "doc_id": doc.id, "primer_name": doc.id, "spec_name": doc.id, "context_pages": [1, 2],
        "preview_pages": [2], "goal": "", "reference_csv": None, "current_round": 0, "last_round": 0,
        "history": [], "created_at": "2026-01-01", "saved_at": None,
    })
    s = SetupSession.load(app, project)
    assert s.state.context_pages == refs(doc, 1, 2) and s.state.preview_pages == refs(doc, 2)


def test_notes_not_mentioned_by_gemini_are_kept(multi):
    app, project, alfa, beta, fake = multi
    fake.setup_answers = [answer(notes=[("[beta]", "nota B")]), answer(notes=[])]
    s = SetupSession.new(app, project, [("alfa", 1), ("beta", 1)])
    s.start()
    s.revise("correggi una colonna")             # Gemini non cita le note: restano
    assert s.document_notes() == {"beta": "nota B"}
