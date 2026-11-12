"""Progetti: più PDF con primer e specifica comuni, note per PDF, CSV unico."""

import re

import pytest

from ragpdf.app import App
from ragpdf.cli import main
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.projects import DocumentSettings, ProjectError
from ragpdf.indexing.extractor import SpecChangedError
from ragpdf.indexing.spec import IndexingSpec, read_csv_rows

SPEC_YAML = """name: motori
row: un codice
columns:
  - name: CODICE
    required: true
output:
  page_column: Pagina
  source_column: File
"""


def responder(req):
    """Ogni pagina ha il codice 'COMUNE' (presente in tutti i PDF) e uno proprio."""
    n = int(re.search(r"pagina (\d+) di", req.prompt).group(1))
    return {"page_summary": "", "rows": [{"codice": "COMUNE"}, {"codice": f"C{n}"}], "notes": ""}


@pytest.fixture
def app(config):
    return App(config, client=FakeGeminiClient(responder, parallelism=2))


def setup_project(app, pdf_factory):
    project = app.projects.create("Motori Diesel")
    alfa = app.add_document(pdf_factory("Alfa.pdf", pages=3, prefix="A"), project=project)
    beta = app.add_document(pdf_factory("Beta.pdf", pages=2, prefix="B"), project=project)
    app.workspace.spec_path("motori").write_text(SPEC_YAML, encoding="utf-8")
    app.workspace.primer_path("motori").write_text("# Primer", encoding="utf-8")
    app.assign_project_rules(project, "motori", "motori")
    return project, alfa, beta


def test_store_basics(app, pdf_factory):
    project = app.projects.create("Motori Diesel")
    assert project.id == "motori-diesel" and app.projects.create("Motori Diesel").id == "motori-diesel-2"
    assert app.projects.get("Motori Diesel").id == "motori-diesel"
    with pytest.raises(ProjectError, match="non trovato"):
        app.projects.get("nessuno")
    doc = app.add_document(pdf_factory("Uno.pdf"), project=project)
    assert app.projects.get(project.id).info.documents == [doc.id]
    assert [p.id for p in app.projects.containing(doc.id)] == ["motori-diesel"]
    # senza progetto: se ne crea uno con lo stesso id del documento
    solo = app.add_document(pdf_factory("Solo.pdf", prefix="S"))
    assert app.projects.get(solo.id).info.documents == [solo.id]


def test_existing_documents_become_projects(config, pdf_factory):
    """Cataloghi aggiunti prima dei progetti: convertiti automaticamente, sessione compresa."""
    first = App(config)
    doc = first.documents.add(pdf_factory("Vecchio.pdf"))          # senza progetto, come prima
    (doc.folder / "setup").mkdir()
    (doc.folder / "setup" / "session.json").write_text("{}", encoding="utf-8")
    doc.info.primer, doc.info.spec = "p", "s"
    doc.save()
    second = App(config)
    project = second.projects.get(doc.id)
    assert project.info.documents == [doc.id] and (project.info.primer, project.info.spec) == ("p", "s")
    assert (project.folder / "setup" / "session.json").exists() and not (doc.folder / "setup").exists()
    App(config)                                                      # idempotente
    assert len(App(config).projects.list()) == 1


def test_document_settings_and_spec(app, pdf_factory):
    project, alfa, beta = setup_project(app, pdf_factory)
    project.set_settings("beta", DocumentSettings(notes="Il codice è in basso.", pages="2"))
    project.set_settings("alfa", DocumentSettings())                 # vuote: non salvate
    project.save()
    project = app.projects.get(project.id)
    assert list(project.info.settings) == ["beta"]
    spec = app.document_spec(project, beta)
    assert "NOTE SPECIFICHE PER QUESTO PDF (Beta)" in spec.instructions and spec.pages == "2"
    assert app.document_spec(project, alfa).instructions == ""
    base = IndexingSpec.model_validate({"name": "x", "row": "r", "columns": [{"name": "A"}]})
    assert base.for_document("", None) is base


def test_project_index_and_merged_csv(app, pdf_factory):
    project, alfa, beta = setup_project(app, pdf_factory)
    extractor = app.extractor()
    for doc in (alfa, beta):
        extractor.run(doc, app.document_spec(project, doc), app.project_primer(project), show_progress=False)
    result = app.export_project(project)
    columns, rows = read_csv_rows(result.csv_path)
    assert result.csv_path.name == "motori-diesel.csv"
    assert columns == ["CODICE", "Pagina", "File"]
    # 'COMUNE' compare su ogni pagina di entrambi i PDF: tutte le occorrenze restano, ognuna con file e pagina
    comune = sorted((r["File"], r["Pagina"]) for r in rows if r["CODICE"] == "COMUNE")
    assert comune == [("Alfa.pdf", "1"), ("Alfa.pdf", "2"), ("Alfa.pdf", "3"), ("Beta.pdf", "1"), ("Beta.pdf", "2")]
    assert len(rows) == 2 * (3 + 2)
    assert result.index.stats["documenti_indicizzati"] == 2
    per_doc = {d.id: r.csv_path for d, r in result.documents}
    assert per_doc["alfa"].parent.name == "documenti" and per_doc["alfa"].exists()


def test_notes_change_requires_only_that_document(app, pdf_factory):
    project, alfa, beta = setup_project(app, pdf_factory)
    extractor, primer = app.extractor(), app.project_primer(project)
    for doc in (alfa, beta):
        extractor.run(doc, app.document_spec(project, doc), primer, show_progress=False)
    project.set_settings("beta", DocumentSettings(notes="nuova nota"))
    project.save()
    extractor.open_manifest(alfa, app.document_spec(project, alfa), primer)        # alfa invariato
    with pytest.raises(SpecChangedError):
        extractor.open_manifest(beta, app.document_spec(project, beta), primer)
    assert [d.id for d in app.export_project(project).stale] == ["beta"]


def test_missing_documents_are_reported(app, pdf_factory):
    project, alfa, beta = setup_project(app, pdf_factory)
    app.extractor().run(alfa, app.document_spec(project, alfa), "", show_progress=False)
    result = app.export_project(project)
    assert [d.id for d in result.not_indexed] == ["beta"]


# ------------------------------------------------------------------ CLI
@pytest.fixture
def run(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"workspace_dir: {(tmp_path / 'data').as_posix()}\nrender:\n  dpi: 72\n", encoding="utf-8")
    monkeypatch.setenv("RAGPDF_FAKE_GEMINI", "1")
    return lambda *args: main(["--config", str(cfg), *args])


def test_project_cli(run, pdf_factory, tmp_path, capsys):
    a, b = pdf_factory("Alfa.pdf", pages=2, prefix="A"), pdf_factory("Beta.pdf", pages=2, prefix="B")
    assert run("project", "new", "Motori", str(a), str(b)) == 0
    assert run("project", "add", "motori", str(a)) == 0                    # già presente: nessun doppione
    assert run("project", "list") == 0
    assert "Motori" in capsys.readouterr().out
    assert run("project", "index", "motori", "-y") == 1                    # manca la specifica
    assert "configurazione" in capsys.readouterr().err
    data = tmp_path / "data"
    (data / "specs" / "motori.yaml").write_text(SPEC_YAML, encoding="utf-8")
    (data / "primers" / "motori.md").write_text("# P", encoding="utf-8")
    app = App(__import__("ragpdf.core.config", fromlist=["load_config"]).load_config(tmp_path / "cfg.yaml"))
    app.assign_project_rules(app.projects.get("motori"), "motori", "motori")
    assert run("project", "note", "motori", "beta", "--notes", "solo per Beta", "--pages", "2") == 0
    assert run("project", "note", "motori", "beta", "--pages", "9") == 1   # fuori dal documento
    assert run("project", "index", "motori", "-y") == 0
    out = capsys.readouterr().out
    assert "CSV unico del progetto" in out and "beta: 1 pagine da elaborare" in out
    assert (data / "exports" / "motori" / "motori.csv").exists()
    assert run("project", "export", "motori") == 0
    assert run("project", "remove-doc", "motori", "beta") == 0
    assert App(load_config_path(tmp_path)).projects.get("motori").info.documents == ["alfa"]


def load_config_path(tmp_path):
    from ragpdf.core.config import load_config

    return load_config(tmp_path / "cfg.yaml")


def test_identical_rows_of_different_documents_are_kept(app, pdf_factory):
    """Senza colonne pagina/file le righe di PDF diversi sarebbero identiche: si tengono comunque."""
    project, alfa, beta = setup_project(app, pdf_factory)
    plain = SPEC_YAML.replace("  page_column: Pagina\n  source_column: File\n", "  page_column:\n  source_column:\n")
    app.workspace.spec_path("motori").write_text(plain, encoding="utf-8")
    for doc in (alfa, beta):
        app.extractor().run(doc, app.document_spec(project, doc), "# Primer", show_progress=False)
    _, rows = read_csv_rows(app.export_project(project).csv_path)
    assert [r["CODICE"] for r in rows].count("COMUNE") == 2       # una per PDF (nel PDF i doppioni sono tolti)


def test_search_page(app, pdf_factory):
    import json

    project, alfa, beta = setup_project(app, pdf_factory)
    for doc in (alfa, beta):
        app.extractor().run(doc, app.document_spec(project, doc), "# Primer", show_progress=False)
    result = app.export_project(project)
    html = result.search_page.read_text(encoding="utf-8")
    assert result.search_page.name == "ricerca.html" and "<title>Ricerca — Motori Diesel</title>" in html
    data = json.loads(html.split("const D = ", 1)[1].split(";\nconst PAGE", 1)[0])
    assert data["columns"] == ["CODICE", "Pagina", "File"] and len(data["rows"]) == 10
    assert data["files"] == {"Alfa.pdf": "alfa", "Beta.pdf": "beta"}
    assert data["page_col"] == 1 and data["source_col"] == 2
    # filtri: colonne con 2..60 valori distinti, esclusa la pagina (qui CODICE ha 4 valori e File 2;
    # in un catalogo vero CODICE ne ha migliaia e non diventa un filtro)
    assert data["filters"] == [0, 2]
    assert data["pdf_link"].startswith("../../documents/")
    assert "</script>" not in html.split("const D = ", 1)[1].split("const PAGE", 1)[0]
