"""Sessione guidata con Gemini simulato."""

import re

import pytest
import yaml

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.indexing.spec import SpecError, load_spec
from ragpdf.setup_session import SetupError, SetupSession, default_context_pages

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


def answer(spec=SPEC_V1, primer="# Primer\nUn catalogo.", pages=(3, 4), summary="Propongo una riga per codice."):
    return {"primer_markdown": primer, "spec_yaml": spec, "summary": summary,
            "questions": ["Serve la colonna versione?"], "preview_pages": list(pages)}


@pytest.fixture
def env(config, pdf_factory):
    fake = FakeCatalog()
    app = App(config, client=FakeGeminiClient(fake, parallelism=2))
    doc = app.documents.add(pdf_factory("Catalogo Test.pdf", pages=8))
    return app, doc, fake


def test_default_context_pages():
    assert default_context_pages(5) == [1, 2, 3, 4, 5]
    pages = default_context_pages(200)
    assert pages[:6] == [1, 2, 3, 4, 5, 6] and len(pages) == 14


def test_start_sends_subset_with_mapping(env):
    app, doc, fake = env
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, doc, [1, 5, 7], goal="voglio i codici")
    assert s.start() == 1
    req = fake.setup_prompts[0]
    assert req.pdf_pages == 3
    assert "pagina 2 dell'estratto = pagina 5 del documento originale" in req.prompt
    assert "voglio i codici" in req.prompt
    assert s.state.preview_pages == [3, 4]
    spec = s.load_spec()
    assert spec.name == doc.id and [c.name for c in spec.columns] == ["MOTORE", "CODICE"]
    assert s.round_notes()["questions"] == ["Serve la colonna versione?"]


def test_without_goal_gemini_is_asked_to_propose(env):
    app, doc, fake = env
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, doc, [1, 2])
    s.start()
    assert "proponi tu" in fake.setup_prompts[0].prompt


def test_invalid_spec_is_retried_once(env):
    app, doc, fake = env
    fake.setup_answers = [answer(spec="columns: [non: valido"), answer()]
    s = SetupSession.new(app, doc, [1, 2])
    assert s.start() == 1
    assert "NON ERA VALIDA" in fake.setup_prompts[1].prompt


def test_twice_invalid_spec_keeps_previous_version(env):
    app, doc, fake = env
    fake.setup_answers = [answer(), answer(spec="row: x"), answer(spec="row: x")]
    s = SetupSession.new(app, doc, [1, 2])
    s.start()
    with pytest.raises(SetupError, match="due volte"):
        s.revise("aggiungi una colonna")
    assert s.state.current_round == 1 and s.state.last_round == 1
    assert not s.round_dir(2).exists()


def test_preview_and_revision_loop(env):
    app, doc, fake = env
    fake.setup_answers = [
        answer(pages=(3, 99)),
        answer(spec=SPEC_V2, pages=(5,), summary="Ho aggiunto la descrizione."),
    ]
    s = SetupSession.new(app, doc, [1, 3, 4])
    s.start()
    assert s.state.preview_pages == [3]           # 99 non esiste: scartata
    preview = s.run_preview(show_progress=False)
    assert [r["CODICE"] for r in preview.index.rows] == ["C3"]
    assert preview.rows_per_page == {3: 1}
    assert s.preview_csv_path.exists()

    assert s.revise("il codice deve essere maiuscolo") == 2
    prompt = fake.setup_prompts[1].prompt
    assert "il codice deve essere maiuscolo" in prompt
    assert "C3" in prompt and "pagina 3: 1 righe" in prompt   # risultati della prova inviati
    assert s.load_spec().columns[1].description == "codice in maiuscolo"
    assert s.state.preview_pages == [5]

    fake.setup_answers = [answer(spec=SPEC_V2)]
    s.revise("altra correzione")
    history_prompt = fake.setup_prompts[2].prompt
    assert "- il codice deve essere maiuscolo" in history_prompt   # osservazioni precedenti


def test_go_back_and_manual_edit(env):
    app, doc, fake = env
    fake.setup_answers = [answer(), answer(spec=SPEC_V2)]
    s = SetupSession.new(app, doc, [1, 2])
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


def test_comparison_with_reference(env, tmp_path):
    app, doc, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text(
        "MOTORE,CODICE,Pagina,File\n"
        "M3,C3,3,Catalogo Test.pdf\n"
        "M3,C3-BIS,3,Catalogo Test.pdf\n"
        "M9,C9,9,Catalogo Test.pdf\n"
        "X,Y,3,Altro.pdf\n",
        encoding="utf-8",
    )
    fake.setup_answers = [answer(pages=(3,))]
    s = SetupSession.new(app, doc, [1, 3], reference_csv=ref)
    s.start()
    assert "M3,C3,3" in fake.setup_prompts[0].prompt          # usato come esempio di formato
    assert s.load_spec().example_csv_path() == ref.resolve()
    preview = s.run_preview(show_progress=False)
    c = preview.comparison
    assert (c.reference_rows, c.matched, len(c.missing), len(c.extra)) == (2, 1, 1, 0)
    assert c.missing == [{"MOTORE": "M3", "CODICE": "C3-BIS"}]


def test_comparison_impossible_without_page_column(env, tmp_path):
    app, doc, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE\nM3,C3\n", encoding="utf-8")
    fake.setup_answers = [answer(pages=(3,))]
    s = SetupSession.new(app, doc, [1, 3], reference_csv=ref)
    s.start()
    preview = s.run_preview(show_progress=False)
    assert preview.comparison is None and "non possibile" in preview.comparison_note


def test_save_writes_workspace_files(env, tmp_path):
    app, doc, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE,Pagina,File\nM,C,1,x.pdf\n", encoding="utf-8")
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, doc, [1, 2], reference_csv=ref, primer_name="tipo-a", spec_name="tipo-a")
    s.start()
    primer_path, spec_path = s.save()
    assert primer_path == app.workspace.primer_path("tipo-a") and primer_path.exists()
    spec = load_spec(spec_path)
    assert spec.name == "tipo-a"
    assert spec.example_csv_path() == spec_path.with_name("tipo-a_esempio.csv")
    assert spec.example_csv_path().exists()
    assert app.documents.get(doc.id).info.primer == "tipo-a"
    assert s.state.saved_at

    # la specifica salvata funziona con l'indicizzazione vera
    report = app.extractor().run(app.documents.get(doc.id), spec, app.primer_text(doc), pages="2-3",
                                 show_progress=False)
    assert report.succeeded == 2


def test_resume_and_start_from_existing(env, pdf_factory):
    app, doc, fake = env
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, doc, [1, 2])
    s.start()
    s.save()
    resumed = SetupSession.load(app, doc)
    assert resumed.state.current_round == 1 and resumed.state.saved_at

    other = app.documents.add(pdf_factory("Secondo catalogo.pdf", pages=4, prefix="B"))
    s2 = SetupSession.new(app, other, [1, 2])
    s2.start_from_existing(doc.id, doc.id)
    assert len(fake.setup_prompts) == 1            # nessuna chiamata a Gemini
    assert s2.load_spec().name == other.id
    assert s2.state.preview_pages                   # pagine di prova di ripiego
    preview = s2.run_preview(show_progress=False)
    assert preview.index.rows


def test_new_session_validation(env, tmp_path):
    app, doc, fake = env
    with pytest.raises(SetupError, match="almeno una"):
        SetupSession.new(app, doc, [])
    with pytest.raises(SetupError, match="massimo"):
        SetupSession.new(app, doc, list(range(1, 50)))
    with pytest.raises(SetupError, match="non trovato"):
        SetupSession.new(app, doc, [1], reference_csv=tmp_path / "manca.csv")


def test_spec_from_gemini_with_code_fences_and_wrong_name(env):
    app, doc, fake = env
    fenced = "```yaml\nname: altro\n" + SPEC_V1.strip() + "\n```"
    fake.setup_answers = [answer(spec=fenced)]
    s = SetupSession.new(app, doc, [1])
    s.start()
    assert yaml.safe_load(s.spec_text())["name"] == doc.id


def test_save_reuses_preview_only_if_rules_unchanged(env, pdf_factory):
    app, doc, fake = env
    fake.setup_answers = [answer(pages=(3, 4))]
    s = SetupSession.new(app, doc, [1, 3, 4])
    s.start()
    s.run_preview(show_progress=False)
    s.save()
    assert app.documents.get(doc.id).info.spec == doc.id
    assert s.reuse_preview() == 2
    manifest = (doc.extraction_dir(doc.id) / "manifest.json")
    assert manifest.exists()

    # se la specifica salvata viene cambiata dopo l'anteprima, niente riuso
    other = SetupSession.new(app, app.documents.add(pdf_factory("altro.pdf", pages=5, prefix="Z")), [1, 2])
    fake.setup_answers = [answer(pages=(2,))]
    other.start()
    other.run_preview(show_progress=False)
    other.save()
    spec_file = app.workspace.spec_path(other.state.spec_name)
    spec_file.write_text(spec_file.read_text(encoding="utf-8") + "instructions: nuova regola\n", encoding="utf-8")
    assert other.reuse_preview() == 0


def test_preview_pages_in_subset_numbering_are_mapped(env):
    app, doc, fake = env
    fake.setup_answers = [answer(pages=(2, 3))]          # numerazione dell'estratto
    s = SetupSession.new(app, doc, [1, 6, 7])
    s.start()
    assert s.state.preview_pages == [6, 7]


def test_manual_edit_keeps_preview_pages(env):
    app, doc, fake = env
    fake.setup_answers = [answer(pages=(6,))]
    s = SetupSession.new(app, doc, [5, 6, 7])
    s.start()
    s.set_preview_pages([1])
    s.manual_edit_done(primer_text="# P")
    assert s.state.preview_pages == [1]


def test_invented_example_csv_is_dropped(env):
    app, doc, fake = env
    fake.setup_answers = [answer(spec=SPEC_V1 + "examples:\n  csv: esempio.csv\n")]
    s = SetupSession.new(app, doc, [1, 2])
    s.start()
    assert s.load_spec().example_csv_path() is None
    s.run_preview(show_progress=False)                  # nessun errore per file mancante


def test_pages_outside_document_trigger_correction(env):
    app, doc, fake = env
    fake.setup_answers = [answer(spec=SPEC_V1 + "pages: '5-300'\n"), answer(spec=SPEC_V1 + "pages: '5-'\n")]
    s = SetupSession.new(app, doc, [1, 2])
    s.start()
    assert "fuori dal documento" in fake.setup_prompts[1].prompt
    assert s.load_spec().pages == "5-"


def test_existing_spec_with_relative_example_csv(env, tmp_path, pdf_factory):
    app, doc, fake = env
    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE,Pagina,File\nM,C,1,x.pdf\n", encoding="utf-8")
    fake.setup_answers = [answer()]
    s = SetupSession.new(app, doc, [1, 2], reference_csv=ref)
    s.start()
    s.save()                                            # salva examples.csv: <nome>_esempio.csv
    other = app.documents.add(pdf_factory("Gemello.pdf", pages=4, prefix="G"))
    s2 = SetupSession.new(app, other, [1, 2])
    s2.start_from_existing(doc.id, doc.id)
    assert s2.load_spec().example_csv_path() == app.workspace.spec_path(doc.id).with_name(f"{doc.id}_esempio.csv")
    s2.run_preview(show_progress=False)
    _, spec_path = s2.save()
    assert load_spec(spec_path).examples.csv == f"{doc.id}_esempio.csv"
