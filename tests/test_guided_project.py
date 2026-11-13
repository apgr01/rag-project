"""Progetto guidato (Gemini simulato): richiesta, prompt, convalida rigorosa, report e progetto."""

import copy

import pytest
from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.library import FieldDef
from ragpdf.guided_project import (
    GuidedError,
    GuidedRequest,
    create_project,
    guided_schema,
    list_runs,
    prepare,
    run_guided,
    validate,
)
from ragpdf.library_analysis import InvalidAnswerError
from ragpdf.web.jobs import ERROR, JobManager
from ragpdf.web.server import create_app

ANSWER = {
    "table": {"description": "Codici ricambio per motore", "row": "una riga per codice FIN",
              "columns": [{"name": "motore", "description": "modello"}, {"name": "codice FIN", "description": ""},
                          {"name": "Motore", "description": "doppione"}]},
    "seed": {"covered": ["motore", "codice FIN"], "missing": [], "collections": ["P/1"], "notes": ""},
    "candidates": [
        {"catalog": "C1", "verdict": "compatibile", "reason": "stesse tabelle", "covered": ["motore", "prezzo"],
         "collections": ["C1/1", "P/2"]},
        {"catalog": "C2", "verdict": "parziale", "reason": "manca il codice", "covered": ["MOTORE"],
         "collections": []},
    ],
    "project_name": "Ricambi  motori",
    "notes": "",
}


class Responder:
    def __init__(self, answer=ANSWER):
        self.answer, self.requests = answer, []

    def __call__(self, request):
        self.requests.append(request)
        return self.answer


@pytest.fixture
def setup(config, pdf_factory):
    responder = Responder()
    app = App(config, client=FakeGeminiClient(responder))
    lib = app.library
    names = ("Alfa", "Beta", "Gamma", "Delta")
    docs = {n: app.add_to_library(pdf_factory(f"{n}.pdf", pages=8, prefix=n)) for n in names}
    card = lib.card(docs["Alfa"])
    card.description = "Motori diesel, tabelle FIN."
    lib.save_card(docs["Alfa"], card)
    lib.add_collection(docs["Alfa"], 1, 1, "copertina")             # non elencata: inutile per i dati
    p1 = lib.add_collection(docs["Alfa"], 2, 4, "dati", "tabelle")
    lib.add_collection(docs["Alfa"], 3, 3, "esempio")
    card = lib.card(docs["Beta"])
    card.description = "Altro catalogo motori."
    lib.save_card(docs["Beta"], card)
    c1 = lib.add_collection(docs["Beta"], 5, 6, "dati", "tabelle")
    card = lib.card(docs["Gamma"])
    card.fields = [FieldDef(name="motore")]                       # solo dati presenti: è elaborato
    lib.save_card(docs["Gamma"], card)
    return app, docs, responder, p1, c1


def test_prepare(setup):
    app, docs, *_ = setup
    ref = app.workspace.root / "references" / "esempio.csv"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("MOTORE;CODICE\nLDW 502;FIN001\n", encoding="utf-8")
    request = GuidedRequest(row="una riga per codice", columns=[{"name": "motore", "description": "modello"}],
                            text="voglio i codici", reference=str(ref))
    prepared = prepare(app, docs["Alfa"], request, "MIE")
    # Delta non è elaborato (né descrizione né dati presenti): escluso
    assert prepared.codes == {"P": "alfa", "C1": "beta", "C2": "gamma"} and prepared.not_elaborated == 1
    assert list(prepared.sections) == ["P/1", "P/2", "C1/1"]
    p = prepared.prompt
    assert "««« INIZIO INDICAZIONI »»»\nMIE\n««« FINE INDICAZIONI »»»" in p
    assert p.index("# Formato della risposta") > p.index("FINE INDICAZIONI »»»\n")
    assert "- Ogni riga rappresenta: una riga per codice" in p and "  1. motore: modello" in p
    assert "CSV d'esempio (esempio.csv), colonne: MOTORE, CODICE" in p and "LDW 502 | FIN001" in p
    assert "- A parole: voglio i codici" in p
    assert "### P — Alfa" in p and "- Sezione P/1: pagine 2-4, Dati — tabelle" in p and "Copertina" not in p
    assert "### C1 — Beta" in p and "- Sezione C1/1: pagine 5-6, Dati" in p and "### C2 — Gamma" in p
    schema = guided_schema(prepared)
    assert schema["properties"]["candidates"]["items"]["properties"]["catalog"]["enum"] == ["C1", "C2"]
    assert schema["properties"]["seed"]["properties"]["collections"]["items"]["enum"] == ["P/1", "P/2"]
    with pytest.raises(GuidedError, match="Descrivi"):
        prepare(app, docs["Alfa"], GuidedRequest(), "")
    # dove cercare: solo una cartella
    app.library.move([docs["Gamma"]], "Motori")
    scoped = prepare(app, docs["Alfa"], GuidedRequest(text="x", folder="Motori"), "")
    assert scoped.codes == {"P": "alfa", "C1": "gamma"}


def test_validate(setup):
    app, docs, _, p1, c1 = setup
    prepared = prepare(app, docs["Alfa"], GuidedRequest(text="x"), "")
    result, problems = validate(ANSWER, prepared)
    assert [c["name"] for c in result.columns] == ["motore", "codice FIN"]
    assert result.seed_collections == [p1.id] and result.project_name == "Ricambi motori"
    beta, gamma = result.candidates
    assert (beta.document, beta.verdict, beta.covered, beta.collections) == ("beta", "compatibile", ["motore"], [c1.id])
    assert (gamma.document, gamma.covered) == ("gamma", ["motore"])          # nome riconosciuto senza maiuscole
    assert any("prezzo" in p for p in problems) and any("P/2" in p for p in problems)
    assert any("Motore" in p for p in problems)

    def broken(change):
        answer = copy.deepcopy(ANSWER)
        change(answer)
        return answer

    cases = [
        (broken(lambda a: a.pop("notes")), "notes: campo mancante"),
        (broken(lambda a: a["seed"].update(extra=1)), "campo non previsto"),
        (broken(lambda a: a["candidates"][0].update(catalog="C9")), "sconosciuto 'C9'"),
        (broken(lambda a: a["candidates"][0].update(catalog="P")), "partenza"),
        (broken(lambda a: a["candidates"][1].update(catalog="C1")), "due volte"),
        (broken(lambda a: a["candidates"][0].update(verdict="forse")), "non è compatibile o parziale"),
        (broken(lambda a: a["candidates"][0].update(collections=["C7/1"])), "sezione sconosciuta 'C7/1'"),
        (broken(lambda a: a["seed"].update(collections=["P/9"])), "sezione sconosciuta 'P/9'"),
        (broken(lambda a: a["table"].update(columns="motore")), "deve essere un elenco"),
        ("testo", "oggetto JSON"),
    ]
    for answer, expected in cases:
        with pytest.raises(InvalidAnswerError, match=expected):
            validate(answer, prepared)


def test_run_and_create(setup):
    app, docs, responder, p1, c1 = setup
    request = GuidedRequest(text="codici per motore")
    run = run_guided(app, app.client(), docs["Alfa"], request, "")
    assert responder.requests[0].kind == "json" and list_runs(app.workspace, seed="alfa")[0].id == run.id
    assert run.candidates == ["beta", "gamma"]

    with pytest.raises(GuidedError, match="partenza"):
        create_project(app, run, "X", "", "", [], "", [{"id": "beta", "collections": []}])
    project = create_project(
        app, run, name="Ricambi", description="Codici", row="una riga per codice",
        columns=[{"name": "motore"}, {"name": "codice FIN"}], instructions="niente spazi",
        documents=[{"id": "alfa", "collections": []}, {"id": "beta", "collections": [c1.id]}])
    info = project.info
    assert info.documents == ["alfa", "beta"] and info.row == "una riga per codice"
    assert info.instructions == "niente spazi"
    assert [f["name"] for f in info.fields] == ["motore", "codice FIN"]
    # partenza senza sezioni scelte: le sue collezioni "dati" di default; Beta: la sezione scelta
    assert project.settings_for("alfa").pages == "2-4" and project.settings_for("alfa").collections == [p1.id]
    assert project.settings_for("beta").pages == "5-6"
    assert list_runs(app.workspace)[0].project_id == project.id
    with pytest.raises(GuidedError, match="esiste già"):
        create_project(app, run, "Altro", "", "", [], "", [{"id": "alfa"}])


def test_api_flow(setup):
    app, docs, responder, p1, c1 = setup
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    ref = client.post("/api/references", files={"file": ("esempio.csv", b"MOTORE,CODICE\nA,1\n", "text/csv")}).json()
    body = {"row": "una riga per codice", "columns": [{"name": "motore"}], "reference": ref["path"],
            "instructions": "X"}
    prev = client.post("/api/library/catalogs/alfa/guided/preview", json=body).json()
    assert prev["candidates"] == 2 and prev["not_elaborated"] == 1 and "»»»\nX\n«««" in prev["prompt"]
    assert responder.requests == []                       # l'anteprima non chiama Gemini
    assert client.post("/api/library/catalogs/alfa/guided/preview", json={}).status_code == 400
    bad = client.post("/api/library/catalogs/alfa/guided/preview", json={"text": "x", "reference": "/etc/passwd"})
    assert bad.status_code == 400

    job = client.post("/api/library/catalogs/alfa/guided", json=body).json()
    assert job["status"] == "completato" and job["kind"] == "guided"
    run_id = job["result"]["run_id"]
    assert client.get("/api/library/catalogs/alfa/guided").json()[0]["id"] == run_id
    view = client.get(f"/api/library/guided/{run_id}").json()
    assert view["catalogs"]["beta"]["collections"][0]["label"] == "Dati" and view["reference_name"] == "esempio.csv"

    r = client.post(f"/api/library/guided/{run_id}/project", json={
        "name": "Ricambi", "row": "una riga per codice", "columns": view["result"]["columns"],
        "documents": [{"id": "alfa", "collections": [p1.id]}, {"id": "beta", "collections": [c1.id]}]}).json()
    pid = r["project"]["id"]
    assert r["run"]["project"]["name"] == "Ricambi" and r["project"]["has_table"] is True
    # il CSV d'esempio passa alla configurazione guidata
    client.post(f"/api/projects/{pid}/setup", json={"context_pages": [["alfa", 2]]})
    assert client.get(f"/api/projects/{pid}/setup").json()["session"]["state"]["reference"] == "esempio.csv"

    responder.answer = {"table": {}}
    job = client.post("/api/library/catalogs/alfa/guided", json={"text": "x"}).json()
    assert job["status"] == ERROR and "non rispetta il formato" in job["error"]
    assert client.get("/api/library/catalogs/alfa/guided").json()[0]["status"] == "non_valida"
