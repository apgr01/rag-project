"""Sessione guidata da terminale, con risposte dell'utente simulate."""

import builtins

import pytest
from test_setup_session import SPEC_V2, FakeCatalog, answer

from ragpdf import ui
from ragpdf.cli import main
from ragpdf.core.fake_gemini import FakeGeminiClient


@pytest.fixture
def terminal(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"workspace_dir: {(tmp_path / 'data').as_posix()}\nrender:\n  dpi: 72\n", encoding="utf-8")
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "open_file", lambda path: True)
    fake = FakeCatalog()
    client = FakeGeminiClient(fake)
    monkeypatch.setattr("ragpdf.app.App.client", lambda self: client)

    def run(inputs, *args):
        answers = iter(inputs)

        def fake_input(prompt=""):
            try:
                value = next(answers)
            except StopIteration:
                raise AssertionError(f"input non previsto: {prompt!r}") from None
            print(prompt + value)
            return value

        monkeypatch.setattr(builtins, "input", fake_input)
        return main(["--config", str(cfg), "setup", *args])

    return run, fake, tmp_path


def test_full_guided_session(terminal, pdf_factory, capsys):
    run, fake, tmp_path = terminal
    pdf = pdf_factory("Catalogo Guidato.pdf", pages=10)
    fake.setup_answers = [answer(pages=(3, 4)), answer(spec=SPEC_V2, summary="Aggiunta descrizione.")]
    inputs = [
        "1-2, 4",                    # pagine da mostrare
        "una riga per codice",       # obiettivo
        "",                          # nessun CSV di riferimento
        "/aiuto",
        "/anteprima 5",
        "/comando-strano",
        "il codice deve essere maiuscolo",
        "/indietro",
        "/ok",
        "n",                         # non avviare l'indicizzazione completa
    ]
    assert run(inputs, f'"{pdf}"') == 0          # percorso con virgolette, come da Windows
    out = capsys.readouterr().out
    assert "Aggiunto" not in out or "Documento aggiunto" in out
    assert "Una riga del CSV = Una riga per ogni codice." in out
    assert "Serve la colonna versione?" in out
    assert "Prova di estrazione sulle pagine 3-4" in out
    assert "Prova di estrazione sulle pagine 5" in out
    assert "Comando sconosciuto" in out
    assert "Aggiunta descrizione." in out
    assert "Versione 1" in out.split("/indietro")[-1]
    assert (tmp_path / "data" / "primers" / "catalogo-guidato.md").exists()
    assert (tmp_path / "data" / "specs" / "catalogo-guidato.yaml").exists()
    # il prompt iniziale contiene le pagine scelte
    assert "pagina 3 dell'estratto = pagina 4 del documento originale" in fake.setup_prompts[0].prompt


def test_resume_session_and_exit(terminal, pdf_factory, capsys):
    run, fake, tmp_path = terminal
    pdf = pdf_factory("Ripresa.pdf", pages=6)
    fake.setup_answers = [answer(pages=(2,))]
    assert run(["", "", "", "/esci"], str(pdf), "--goal", "x") == 0
    assert "riprendila con" in capsys.readouterr().out
    # seconda volta: il PDF è già presente, la sessione viene ripresa senza nuove chiamate
    assert run(["s", "/ok", "n"], str(pdf)) == 0
    assert len(fake.setup_prompts) == 1
    assert (tmp_path / "data" / "specs" / "ripresa.yaml").exists()


def test_save_asks_before_overwriting(terminal, pdf_factory, capsys):
    run, fake, tmp_path = terminal
    pdf = pdf_factory("Uno.pdf", pages=4)
    specs = tmp_path / "data" / "specs"
    specs.mkdir(parents=True)
    (specs / "uno.yaml").write_text("vecchia", encoding="utf-8")
    fake.setup_answers = [answer(pages=(2,))]
    assert run(["", "", "", "/ok", "n", "uno-v2", "n"], str(pdf)) == 0
    assert (specs / "uno.yaml").read_text(encoding="utf-8") == "vecchia"
    assert (specs / "uno-v2.yaml").exists()


def test_start_from_existing_files(terminal, pdf_factory, capsys):
    run, fake, tmp_path = terminal
    fake.setup_answers = [answer(pages=(2,))]
    assert run(["", "", "", "/ok", "n"], str(pdf_factory("Primo.pdf", pages=4))) == 0
    second = pdf_factory("Secondo.pdf", pages=4, prefix="S")
    # menu "Da dove vuoi partire?" -> 2 (esistenti), primer 1, specifica 1
    assert run(["2", "1", "1", "/ok", "n"], str(second)) == 0
    assert len(fake.setup_prompts) == 1   # nessuna nuova analisi
    assert (tmp_path / "data" / "specs" / "secondo.yaml").exists()


def test_not_interactive(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"workspace_dir: {(tmp_path / 'data').as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(ui, "interactive", lambda: False)
    assert main(["--config", str(cfg), "setup", "x"]) == 1
    assert "terminale interattivo" in capsys.readouterr().err


def test_simulated_mode_works_end_to_end(monkeypatch, tmp_path, pdf_factory):
    """Con RAGPDF_FAKE_GEMINI=1 anche la sessione guidata deve funzionare."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"workspace_dir: {(tmp_path / 'data').as_posix()}\nrender:\n  dpi: 72\n", encoding="utf-8")
    monkeypatch.setenv("RAGPDF_FAKE_GEMINI", "1")
    monkeypatch.setattr(ui, "interactive", lambda: True)
    answers = iter(["", "", "", "/ok", "n"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    assert main(["--config", str(cfg), "setup", str(pdf_factory("Sim.pdf", pages=3))]) == 0
