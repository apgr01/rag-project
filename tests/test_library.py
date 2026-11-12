"""Biblioteca dei cataloghi: cartelle, tag, campi, collezioni di pagine e passaggio ai progetti."""

import pytest
import yaml
from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.library import LibraryCard, LibraryError, normalize_folder, normalize_tags
from ragpdf.web.jobs import JobManager
from ragpdf.web.server import create_app


@pytest.fixture
def app(config):
    return App(config, client=FakeGeminiClient(None))


@pytest.fixture
def client(app):
    return TestClient(create_app(app, JobManager(run_inline=True)))


def upload(client, pdf, folder=""):
    with open(pdf, "rb") as f:
        r = client.post("/api/library/catalogs", files={"file": (pdf.name, f, "application/pdf")},
                        data={"folder": folder})
    assert r.status_code == 200, r.text
    return r.json()


def test_normalization():
    assert normalize_folder(" Motori / Lombardini/ ") == "Motori/Lombardini"
    assert normalize_folder("Motori\\VM") == "Motori/VM"
    assert normalize_folder("") == ""
    with pytest.raises(LibraryError):
        normalize_folder("Motori/../x")
    assert normalize_tags([" Motori ", "motori", "tabelle  FIN", ""]) == ["Motori", "tabelle FIN"]


def test_library_lives_without_projects(app, pdf_factory):
    doc = app.add_to_library(pdf_factory("Alfa.pdf", pages=5), folder="Motori/Lombardini")
    assert app.projects.containing(doc.id) == []
    assert app.library.card(doc).folder == "Motori/Lombardini"
    assert app.library.folders() == ["Motori", "Motori/Lombardini"]
    # al riavvio non diventa un progetto (la scheda c'è)
    App(app.config, client=app._client)
    assert app.projects.list() == []


def test_old_documents_are_migrated(app, pdf_factory):
    # documento di una versione precedente: senza scheda e senza progetto
    doc = app.documents.add(pdf_factory("Vecchio.pdf"))
    in_project = app.add_document(pdf_factory("Nel progetto.pdf"))
    app.library.card_path(in_project).unlink()
    again = App(app.config, client=app._client)
    assert [p.id for p in again.projects.containing(doc.id)] == [doc.id]   # come prima
    assert again.library.has_card(doc) and again.library.has_card(in_project)
    # rilanciando non si creano altri progetti
    App(app.config, client=app._client)
    assert len(again.projects.list()) == 2


def test_collections(app, pdf_factory):
    doc = app.add_to_library(pdf_factory("Alfa.pdf", pages=10))
    lib = app.library
    a = lib.add_collection(doc, 9, 3, "da-indicizzare", " tabelle ")     # ordine invertito: corretto
    assert (a.start, a.end, a.comment) == (3, 9, "tabelle")
    lib.add_collection(doc, 1, 1, "copertina")
    lib.add_collection(doc, 4, 5, "esempio")
    card = lib.card(doc)
    assert [c.tag for c in card.collections] == ["copertina", "da-indicizzare", "esempio"]   # per pagina
    assert card.pages_with_tag("da-indicizzare") == list(range(3, 10))
    with pytest.raises(LibraryError, match="fuori"):
        lib.add_collection(doc, 8, 11, "indice")
    with pytest.raises(ValueError):
        lib.add_collection(doc, 1, 2, "inventato")
    lib.update_collection(doc, a.id, 3, 4, "tabelle-tecniche", "")
    assert lib.card(doc).pages_with_tag("da-indicizzare") == []
    lib.remove_collection(doc, a.id)
    with pytest.raises(LibraryError):
        lib.remove_collection(doc, a.id)
    assert len(lib.card(doc).collections) == 2


def test_folders(app, pdf_factory):
    lib = app.library
    a = app.add_to_library(pdf_factory("Alfa.pdf"), folder="Motori/Lombardini")
    b = app.add_to_library(pdf_factory("Beta.pdf"), folder="Motori")
    lib.create_folder("Trattori")
    assert lib.folders() == ["Motori", "Motori/Lombardini", "Trattori"]
    lib.rename_folder("Motori", "Archivio/Motori")
    assert lib.card(a).folder == "Archivio/Motori/Lombardini"
    assert lib.card(b).folder == "Archivio/Motori"
    assert "Motori" not in lib.folders() and "Archivio" in lib.folders()
    with pytest.raises(LibraryError, match="dentro sé stessa"):
        lib.rename_folder("Archivio", "Archivio/x")
    with pytest.raises(LibraryError, match="Esiste già"):
        lib.rename_folder("Archivio/Motori", "Trattori")
    with pytest.raises(LibraryError, match="contiene 2 cataloghi"):
        lib.delete_folder("Archivio")
    lib.move([a, b], "")
    lib.delete_folder("Archivio")
    assert lib.folders() == ["Trattori"]


def test_delete_only_outside_projects(app, pdf_factory):
    doc = app.add_to_library(pdf_factory("Alfa.pdf"))
    project = app.create_project_from_library("P", [doc])
    with pytest.raises(LibraryError, match="toglilo prima"):
        app.delete_document(doc)
    app.remove_project(project)
    assert app.documents.list()               # il PDF resta
    app.delete_document(doc)
    assert app.documents.list() == []


def test_api_library(client, app, pdf_factory):
    alfa_pdf = pdf_factory("Alfa.pdf", pages=12)
    alfa = upload(client, alfa_pdf, folder="Motori")
    upload(client, pdf_factory("Beta.pdf", pages=4), folder="Motori/VM")
    copy = alfa_pdf.with_name("Copia di Alfa.pdf")
    copy.write_bytes(alfa_pdf.read_bytes())
    again = upload(client, copy)                                    # stesso contenuto, altro nome
    assert again["already_present"] and again["id"] == alfa["id"] and again["folder"] == "Motori"
    bad = client.post("/api/library/catalogs", files={"file": ("x.txt", b"x", "text/plain")})
    assert bad.status_code == 400

    r = client.put("/api/library/catalogs/alfa", json={
        "title": "Catalogo Alfa 2024", "description": "Motori agricoli", "tags": ["motori", "Motori", "FIN"],
        "fields": [{"name": "motore", "description": "modello"}, {"name": "alesaggio"}, {"name": " "}]}).json()
    assert r["title"] == "Catalogo Alfa 2024" and r["tags"] == ["motori", "FIN"]
    assert [f["name"] for f in r["fields"]] == ["motore", "alesaggio"]
    dup = client.put("/api/library/catalogs/alfa", json={"fields": [{"name": "a"}, {"name": "A"}]})
    assert dup.status_code == 400

    r = client.post("/api/library/catalogs/alfa/collections",
                    json={"start": 3, "end": 10, "tag": "da-indicizzare", "comment": "tabelle"}).json()
    cid = r["collections"][0]["id"]
    client.post("/api/library/catalogs/alfa/collections", json={"start": 4, "end": 5, "tag": "esempio"})
    assert client.post("/api/library/catalogs/alfa/collections",
                       json={"start": 3, "end": 30, "tag": "indice"}).status_code == 400
    assert client.put(f"/api/library/catalogs/alfa/collections/{cid}",
                      json={"start": 3, "end": 11, "tag": "da-indicizzare"}).json()["to_index"] == 9

    client.post("/api/library/tags", json={"ids": ["alfa", "beta"], "add": ["da studiare"], "remove": ["fin"]})
    overview = client.get("/api/library/catalogs").json()
    assert overview["folders"] == ["Motori", "Motori/VM"]
    assert {t["name"]: t["count"] for t in overview["tags"]} == {"da studiare": 2, "motori": 1}
    assert [t["id"] for t in overview["page_tags"]][:2] == ["da-indicizzare", "esempio"]

    assert client.post("/api/library/folders", json={"path": "Nuova"}).json()["folder"] == "Nuova"
    client.post("/api/library/move", json={"ids": ["beta"], "folder": "Nuova"})
    assert client.put("/api/library/folders", json={"path": "Nuova", "new_path": "Altro"}).json()["folder"] == "Altro"
    assert client.delete("/api/library/folders", params={"path": "Altro"}).status_code == 400
    assert client.post("/api/library/move", json={"ids": []}).status_code == 400

    # progetto dalla selezione: pagine da indicizzare, pagine d'esempio e campi passano al progetto
    project = client.post("/api/library/projects", json={"name": "Motori", "ids": ["alfa", "beta"]}).json()
    stored = yaml.safe_load((app.projects.get(project["id"]).folder / "project.yaml").read_text(encoding="utf-8"))
    assert stored["settings"] == {"alfa": {"notes": "", "pages": "3-11"}}
    setup = client.get(f"/api/projects/{project['id']}/setup").json()
    assert setup["suggested_pages"] == [["alfa", 4], ["alfa", 5]]
    assert "- motore: modello" in setup["suggested_goal"] and "Motori agricoli" in setup["suggested_goal"]
    assert client.delete("/api/library/catalogs/alfa").status_code == 400      # usato dal progetto

    other = client.post("/api/projects", json={"name": "Altro"}).json()
    added = client.post(f"/api/projects/{other['id']}/documents/from-library", json={"ids": ["alfa", "beta"]})
    assert added.json()["added"] == ["alfa", "beta"]
    assert client.post(f"/api/projects/{other['id']}/documents/from-library",
                       json={"ids": ["alfa"]}).json()["added"] == []
    summary = client.get("/api/library/catalogs/alfa").json()
    assert {p["name"] for p in summary["projects"]} == {"Motori", "Altro"}


def test_card_defaults():
    card = LibraryCard()
    assert card.folder == "" and card.pages_with_tag("esempio") == []
