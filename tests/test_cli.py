"""Prova dei comandi principali come li userebbe una persona (Gemini simulato)."""

import pytest

from ragpdf.cli import main


@pytest.fixture
def run(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"workspace_dir: {(tmp_path / 'data').as_posix()}\nrender:\n  dpi: 72\n", encoding="utf-8")
    monkeypatch.setenv("RAGPDF_FAKE_GEMINI", "1")

    def _run(*args):
        return main(["--config", str(cfg), *args])

    return _run


def test_typical_session(run, pdf_factory, tmp_path, capsys):
    pdf = pdf_factory("Catalogo Ricambi.pdf", pages=2)
    assert run("doctor") == 0
    assert run("add", str(pdf), "--no-primer") == 0
    assert run("add", str(pdf), "--no-primer") == 1            # duplicato
    assert run("primer", "generate", "ricambi") == 0
    assert run("spec", "new", "ricambi") == 0
    assert run("spec", "check", "ricambi", "--show-prompt", "ricambi", "--page", "2") == 0
    assert run("index", "ricambi", "-y") == 0
    assert run("export", "--all") == 0
    assert run("list") == 0
    out = capsys.readouterr().out
    assert "catalogo-ricambi" in out
    assert (tmp_path / "data" / "exports" / "ricambi" / "catalogo-ricambi.csv").exists()


def test_index_without_spec_explains_what_to_do(run, pdf_factory, capsys):
    run("add", str(pdf_factory()), "--no-primer")
    assert run("index", "catalogo") == 1
    assert "ragpdf spec new" in capsys.readouterr().err


def test_unknown_document(run, capsys):
    assert run("index", "nessuno", "--spec", "x") == 1
    assert "ragpdf add" in capsys.readouterr().err


def test_index_without_primer_non_interactive_cancels(run, pdf_factory, capsys):
    run("add", str(pdf_factory()), "--no-primer")
    run("spec", "new", "s")
    assert run("index", "catalogo") == 1      # senza -y e senza terminale: annulla
    assert "primer generate" in capsys.readouterr().out


def test_spec_draft_with_simulated_gemini(run, pdf_factory, tmp_path, monkeypatch):
    """Il client simulato restituisce un segnaposto non valido: la bozza viene
    rifiutata, ma il file resta su disco per la correzione manuale."""
    run("add", str(pdf_factory(pages=6)), "--no-primer")
    assert run("spec", "draft", "bozza", "--request", "una riga per codice", "--doc", "catalogo") == 1
    assert (tmp_path / "data" / "specs" / "bozza.yaml").exists()


def test_compare_command(run, pdf_factory, tmp_path, capsys):
    run("add", str(pdf_factory("Cat.pdf", pages=2)), "--no-primer")
    run("spec", "new", "s")
    run("index", "cat", "--spec", "s", "-y")
    ref = tmp_path / "rif.csv"
    ref.write_text("CODICE,PAGINA_PDF,FILE_ORIGINE\n[simulato] codice,1,Cat.pdf\nALTRO,9,Cat.pdf\n", encoding="utf-8")
    assert run("compare", "cat", "--spec", "s", "--reference", str(ref), "--key", "CODICE") == 0
    out = capsys.readouterr().out
    assert "Copertura: 100.0%" in out          # la riga a pagina 9 è esclusa (non elaborata)
    assert (tmp_path / "data" / "exports" / "s" / "cat_confronto.csv").exists()


def test_primer_generate_with_selected_pages(run, pdf_factory, tmp_path):
    run("add", str(pdf_factory("Grande.pdf", pages=12)), "--no-primer")
    assert run("primer", "generate", "grande", "--pages", "1,4,10-11") == 0
    assert (tmp_path / "data" / "primers" / "grande.md").exists()
