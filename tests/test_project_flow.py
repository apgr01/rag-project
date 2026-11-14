"""Il percorso del progetto in 5 passi: stato, prossimo passo, note proposte, colonne riportate."""

from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.web.jobs import DONE, JobManager
from ragpdf.web.server import create_app


def states(project):
    return {k: v["state"] for k, v in project["steps"].items()}


def test_project_flow(config, pdf_factory):
    app = App(config, client=FakeGeminiClient())           # specifica simulata: colonne CODICE, DESCRIZIONE
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    pid = client.post("/api/projects", json={"name": "Motori"}).json()["id"]
    base = f"/api/projects/{pid}"
    get = lambda: client.get(base).json()                  # noqa: E731

    p = get()
    assert p["next"]["tab"] == "docs" and states(p)["docs"] == "da fare"

    for name in ("Alfa", "Beta"):
        doc = app.add_to_library(pdf_factory(f"{name}.pdf", pages=3, prefix=name))
        client.post(f"{base}/documents/from-library", json={"ids": [doc.id]})
    client.put(f"{base}/documents/beta/settings", json={"notes": "nota mia"})
    p = get()
    assert p["next"]["tab"] == "settings" and states(p)["settings"] == "facoltativo"
    assert states(p)["index"] == "da fare" and p["progress"] is None

    # regole senza tabella: Gemini propone le colonne
    client.post(f"{base}/setup", json={"context_pages": [["alfa", 1]]})
    assert client.post(f"{base}/setup/start").json()["status"] == DONE
    p = get()
    assert states(p)["setup"] == "in corso"
    assert p["next"] == {"tab": "setup", "text": "Controlla la versione 1 e salva le regole"}

    # note di questa versione: proposte rispetto a quelle del progetto (passo 2)
    client.put(f"{base}/setup/notes", json={"notes": {"alfa": "proposta per Alfa", "beta": "nota di Gemini"}})
    proposals = client.get(f"{base}/setup").json()["session"]["round"]["note_proposals"]
    assert [(x["document"], x["current"], x["proposed"]) for x in proposals] == [
        ("alfa", "", "proposta per Alfa"), ("beta", "nota mia", "nota di Gemini")]
    client.post(f"{base}/setup/save", json={"overwrite": True, "apply_notes": ["alfa"]})
    p = get()
    notes = {d["id"]: d["settings"]["notes"] for d in p["documents"]}
    assert notes == {"alfa": "proposta per Alfa", "beta": "nota mia"}      # Beta: rifiutata, resta la tua
    # la tabella era vuota: le colonne e la riga delle regole tornano al passo 1
    assert [f["name"] for f in p["fields"]] == ["CODICE", "DESCRIZIONE"] and p["row"]
    assert states(p) == {"settings": "fatto", "docs": "fatto", "setup": "fatto", "index": "da fare",
                         "results": "da fare"}
    assert p["next"]["tab"] == "index" and p["progress"]["to_index"] == 6

    assert client.post(f"{base}/index", json={}).json()["status"] == DONE
    p = get()
    assert states(p)["index"] == "fatto" and p["next"]["tab"] == "results" and p["progress"]["done"] == 6

    # colonne cambiate dopo le regole: il passo 3 va rivisto
    client.put(f"{base}/settings", json={"columns": [{"name": "CODICE"}, {"name": "PREZZO"}]})
    p = get()
    assert states(p)["setup"] == "da rivedere" and p["next"]["tab"] == "setup"
    client.put(f"{base}/settings", json={"columns": [{"name": "CODICE"}, {"name": "DESCRIZIONE"}]})
    # regole di un PDF cambiate dopo l'indicizzazione: il passo 4 va rifatto per quel PDF
    client.put(f"{base}/documents/beta/settings", json={"notes": "nuova regola"})
    p = get()
    assert states(p)["index"] == "da rivedere" and p["progress"]["stale"] == ["beta"]
    assert p["next"] == {"tab": "index", "text": "Rifai i PDF con le regole cambiate"}


def test_cli_save_applies_all_notes(config, pdf_factory):
    """Dalla riga di comando (nessuna scelta) le note della versione salvata si applicano tutte."""
    from ragpdf.setup_session import SetupSession

    app = App(config, client=FakeGeminiClient())
    doc = app.add_to_library(pdf_factory("Alfa.pdf", pages=2))
    project = app.create_project_from_library("P", [doc])
    session = SetupSession.new(app, project, [(doc.id, 1)])
    session.start()
    session.manual_edit_done(document_notes={"alfa": "nota"})
    session.save()
    assert app.projects.get(project.id).settings_for("alfa").notes == "nota"
