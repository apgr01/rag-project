"""Pagine da indicizzare prese dalle collezioni della biblioteca: copia, differenze, aggiornamento."""

import pytest
from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.library import LibraryError
from ragpdf.web.jobs import JobManager
from ragpdf.web.server import create_app


@pytest.fixture
def setup(config, pdf_factory):
    app = App(config, client=FakeGeminiClient())
    doc = app.add_to_library(pdf_factory("Alfa.pdf", pages=14))
    lib = app.library
    a = lib.add_collection(doc, 3, 5, "dati", "tabelle motori")
    b = lib.add_collection(doc, 8, 9, "dati", "tabelle accessori")
    lib.add_collection(doc, 1, 1, "indice")
    lib.add_collection(doc, 2, 2, "legenda")
    lib.add_collection(doc, 4, 4, "esempio")
    return app, doc, a, b


def test_pages_are_copied_and_changes_reported(setup):
    app, doc, a, b = setup
    project = app.create_project_from_library("Motori", [doc])
    settings = project.settings_for(doc.id)
    assert settings.pages == "3-5, 8-9" and settings.collections == [a.id, b.id]
    assert [c["changed"] for c in app.library_changes(project)] == [False]

    # la biblioteca cambia: il progetto no (le pagine sono una copia), ma lo segnala
    app.library.update_collection(doc, a.id, 3, 6, "dati", "tabelle motori")
    app.library.remove_collection(doc, b.id)
    c = app.library.add_collection(doc, 12, 12, "dati", "nuove tabelle")
    change = app.library_changes(project)[0]
    assert project.settings_for(doc.id).pages == "3-5, 8-9"
    assert change["changed"] and change["library_pages"] == "3-6" and change["missing"] == 1
    assert [x["id"] for x in change["new_data"]] == [c.id]          # solo un suggerimento

    assert app.update_pages_from_library(project, [doc.id]) == [doc.id]
    settings = project.settings_for(doc.id)
    assert settings.pages == "3-6" and settings.collections == [a.id]
    assert app.library_changes(project)[0]["changed"] is False
    app.library.remove_collection(doc, a.id)
    with pytest.raises(LibraryError, match="nessuna delle collezioni"):
        app.update_pages_from_library(project, [doc.id])


def test_suggested_context_pages(setup, pdf_factory):
    app, doc, *_ = setup
    other = app.add_to_library(pdf_factory("Beta.pdf", pages=6))
    app.library.add_collection(other, 5, 6, "esempio")
    refs = app.suggested_context_refs([doc, other], limit=40)
    # indice, legenda ed esempi di ciascun PDF, presi a turno
    assert refs == [("alfa", 1), ("beta", 5), ("alfa", 2), ("beta", 6), ("alfa", 4)]
    assert app.suggested_context_refs([doc, other], limit=2) == [("alfa", 1), ("beta", 5)]


def test_api(setup):
    app, doc, a, b = setup
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    project = client.post("/api/library/projects", json={"name": "Motori", "ids": [doc.id]}).json()
    pid = project["id"]
    collections = project["documents"][0]["library"]["collections"]
    assert [x["tag"] for x in collections] == ["indice", "legenda", "dati", "esempio", "dati"]
    assert project["library_changes"] == []
    setup_view = client.get(f"/api/projects/{pid}/setup").json()
    assert setup_view["suggested_pages"] == [["alfa", 1], ["alfa", 2], ["alfa", 4]]

    # scelta delle collezioni nel progetto: le pagine le calcola il programma
    url = f"/api/projects/{pid}/documents/{doc.id}/settings"
    s = client.put(url, json={"notes": "n", "collections": [b.id], "pages": "1-14"}).json()
    assert s["settings"] == {"notes": "n", "pages": "8-9", "collections": [b.id]}
    assert client.put(url, json={"collections": ["inesistente"]}).status_code == 400
    s = client.put(url, json={"pages": "2-"}).json()                 # pagine scritte a mano
    assert s["settings"]["pages"] == "2-" and s["settings"]["collections"] == []

    client.put(url, json={"collections": [b.id]})
    app.library.update_collection(doc, b.id, 8, 10, "dati", "")
    project = client.get(f"/api/projects/{pid}").json()
    assert [(c["doc_id"], c["pages"], c["library_pages"]) for c in project["library_changes"]] == [
        ("alfa", "8-9", "8-10")]
    r = client.post(f"/api/projects/{pid}/library-update", json={"docs": [doc.id]}).json()
    assert r["updated"] == ["alfa"] and r["project"]["library_changes"] == []
    assert r["project"]["documents"][0]["settings"]["pages"] == "8-10"
