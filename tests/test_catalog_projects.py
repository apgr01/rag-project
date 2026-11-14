"""Nella scheda di un PDF: i progetti che lo usano e lo stato della sua indicizzazione."""

from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.web.jobs import DONE, JobManager
from ragpdf.web.server import create_app


def test_catalog_projects(config, pdf_factory):
    app = App(config, client=FakeGeminiClient())          # specifica e righe simulate
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    doc = app.add_to_library(pdf_factory("Alfa.pdf", pages=6))
    app.library.add_collection(doc, 2, 4, "dati", "tabelle")
    assert client.get("/api/library/catalogs/alfa/projects").json() == []

    first = client.post("/api/library/projects", json={"name": "Motori", "ids": ["alfa"]}).json()["id"]
    client.post("/api/library/projects", json={"name": "Componenti", "ids": ["alfa"]})
    items = {p["name"]: p for p in client.get("/api/library/catalogs/alfa/projects").json()}
    assert set(items) == {"Motori", "Componenti"}
    assert items["Motori"]["configured"] is False and items["Motori"]["pages"] == "2-4"
    assert items["Motori"]["from_library"] is True and items["Motori"]["csv"] is False

    # configurazione e indicizzazione del primo progetto
    base = f"/api/projects/{first}"
    client.post(f"{base}/setup", json={"context_pages": [["alfa", 2]]})
    assert client.post(f"{base}/setup/start").json()["status"] == DONE
    client.post(f"{base}/setup/save", json={"overwrite": True})
    assert client.post(f"{base}/index", json={}).json()["status"] == DONE

    items = {p["name"]: p for p in client.get("/api/library/catalogs/alfa/projects").json()}
    motori = items["Motori"]
    # salvare la configurazione non perde il legame con le collezioni della biblioteca
    assert motori["from_library"] is True
    assert motori["configured"] and motori["to_index"] == 3 and motori["done"] == 3 and motori["failed"] == 0
    assert motori["csv"] is True and isinstance(motori["rows"], int) and motori["stale"] is False
    assert motori["job"] is None and items["Componenti"]["configured"] is False
    # il CSV di quel PDF si scarica dalla scheda
    csv = client.get(f"{base}/download/documento", params={"doc": "alfa"})
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]

    # cambiano le regole di quel PDF (note): i suoi risultati sono da rifare
    client.put(f"{base}/documents/alfa/settings", json={"notes": "nuova regola", "pages": "2-4"})
    motori = {p["name"]: p for p in client.get("/api/library/catalogs/alfa/projects").json()}["Motori"]
    assert motori["stale"] is True
