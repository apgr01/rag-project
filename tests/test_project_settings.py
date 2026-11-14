"""Impostazioni globali del progetto: la tabella decisa dall'utente e il controllo delle colonne."""

import pytest
from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.projects import ProjectError, clean_columns, compare_columns
from ragpdf.web.jobs import JobManager
from ragpdf.web.server import create_app


@pytest.fixture
def client_app(config):
    app = App(config, client=FakeGeminiClient())          # specifica simulata: colonne CODICE, DESCRIZIONE
    return TestClient(create_app(app, JobManager(run_inline=True))), app


def test_compare_and_clean_columns():
    assert compare_columns(["Codice", "descrizione"], ["CODICE", " Descrizione "])["ok"] is True
    check = compare_columns(["CODICE", "PREZZO"], ["CODICE", "DESCRIZIONE"])
    assert check == {"ok": False, "missing": ["PREZZO"], "extra": ["DESCRIZIONE"], "order_ok": True}
    assert compare_columns(["A", "B"], ["B", "A"]) == {"ok": False, "missing": [], "extra": [], "order_ok": False}
    assert clean_columns([{"name": " motore  base "}, {"name": ""}, {"name": "x", "description": " a  b "}]) == [
        {"name": "motore base", "description": ""}, {"name": "x", "description": "a b"}]
    with pytest.raises(ProjectError, match="due volte"):
        clean_columns([{"name": "Motore"}, {"name": "motore"}])


def test_settings_prompt_and_checks(client_app, pdf_factory):
    client, app = client_app
    doc = app.add_to_library(pdf_factory("Alfa.pdf", pages=4))
    card = app.library.card(doc)
    card.description = "Motori diesel."
    card.fields = []
    app.library.save_card(doc, card)
    project = client.post("/api/library/projects", json={"name": "Motori", "ids": [doc.id]}).json()
    pid = project["id"]
    assert project["has_table"] is False and project["columns_check"] is None
    # i PDF del progetto mostrano quello che dice la biblioteca
    assert project["documents"][0]["library"]["description"] == "Motori diesel."

    body = {"description": "Indice dei codici", "row": "una riga per codice",
            "columns": [{"name": "CODICE", "description": "codice articolo"}, {"name": "PREZZO"}],
            "instructions": "Se manca il prezzo lascia vuoto."}
    project = client.put(f"/api/projects/{pid}/settings", json=body).json()
    assert project["has_table"] is True and project["row"] == "una riga per codice"
    assert [c["name"] for c in project["fields"]] == ["CODICE", "PREZZO"]
    bad = client.put(f"/api/projects/{pid}/settings", json={"columns": [{"name": "a"}, {"name": "A"}]})
    assert bad.status_code == 400

    # la configurazione guidata riceve la tabella come vincolo
    client.post(f"/api/projects/{pid}/setup", json={"context_pages": [[doc.id, 1]], "goal": "solo pagine pari"})
    prompt = client.get(f"/api/projects/{pid}/setup/prompt").json()["prompt"]
    assert "Ogni riga rappresenta: una riga per codice" in prompt
    assert "Colonne OBBLIGATORIE" in prompt and "  1. CODICE: codice articolo\n  2. PREZZO" in prompt
    assert "Altre indicazioni: Se manca il prezzo" in prompt
    assert "solo pagine pari" in prompt          # obiettivo delle configurazioni già avviate
    assert "SCHEDA DEL DOCUMENTO (dalla biblioteca)" in prompt and "Descrizione: Motori diesel." in prompt

    # Gemini (simulato) propone CODICE, DESCRIZIONE: il giro lo segnala
    client.post(f"/api/projects/{pid}/setup/start")
    view = client.get(f"/api/projects/{pid}/setup").json()["session"]
    assert view["round"]["columns_check"] == {"ok": False, "missing": ["PREZZO"], "extra": ["DESCRIZIONE"],
                                              "order_ok": True}
    # salvata la configurazione, anche il riepilogo del progetto lo segnala
    client.post(f"/api/projects/{pid}/setup/save", json={"overwrite": True})
    assert client.get(f"/api/projects/{pid}").json()["columns_check"]["missing"] == ["PREZZO"]
    project = client.put(f"/api/projects/{pid}/settings",
                         json={"columns": [{"name": "codice"}, {"name": "descrizione"}]}).json()
    assert project["columns_check"]["ok"] is True
