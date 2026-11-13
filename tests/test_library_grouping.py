"""Raggruppamento dei cataloghi con Gemini (simulato): prompt, convalida rigorosa, report e azioni."""

import copy

import pytest
from fastapi.testclient import TestClient

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.library_analysis import InvalidAnswerError
from ragpdf.library_grouping import (
    GroupingError,
    grouping_schema,
    list_groupings,
    move_document,
    prepare,
    run_grouping,
    validate,
)
from ragpdf.web.jobs import ERROR, JobManager
from ragpdf.web.server import create_app

ANSWER = {
    "groups": [
        {"name": "Motori diesel", "catalogs": ["C1", "C2"], "comment": "stesse tabelle FIN",
         "common_tags": ["Motori", "diesel"],
         "fields": [{"name": "motore", "description": "modello"}, {"name": "codice FIN", "description": ""},
                    {"name": "Motore", "description": "doppione"}]},
        {"name": "Solo", "catalogs": ["C3"], "comment": "da solo", "common_tags": [], "fields": []},
    ],
    "ungrouped": [],
    "notes": "C4 era poco descritto.",
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
    docs = [app.add_to_library(pdf_factory(f"{n}.pdf", pages=6, prefix=n)) for n in ("Alfa", "Beta", "Gamma", "Delta")]
    lib = app.library
    card = lib.card(docs[0])
    card.description, card.tags = "Motori diesel, tabelle FIN.", ["motori"]
    lib.save_card(docs[0], card)
    lib.add_collection(docs[0], 2, 3, "esempio")
    lib.add_collection(docs[1], 4, 6, "dati")
    return app, docs, responder


def test_prepare_prompt(setup):
    app, docs, _ = setup
    app.create_project_from_library("Vecchio", [docs[1]])
    prepared = prepare(app, docs, "MIE INDICAZIONI", with_images=True)
    assert prepared.codes == {"C1": "alfa", "C2": "beta", "C3": "gamma", "C4": "delta"}
    p = prepared.prompt
    assert "««« INIZIO INDICAZIONI »»»\nMIE INDICAZIONI\n««« FINE INDICAZIONI »»»" in p
    assert p.index("# Formato della risposta") > p.index("FINE INDICAZIONI »»»\n")
    assert "### C1 — Alfa" in p and "Descrizione: Motori diesel" in p and "Pagine: esempio 2-3" in p
    assert "Già nei progetti: Vecchio" in p and "non ancora studiato" in p
    # immagini: pagina d'esempio, altrimenti prima pagina da indicizzare; niente per chi non ha pagine segnate
    assert prepared.images == [("alfa", 2), ("beta", 4)]
    assert "- immagine 1: C1, pagina 2" in p and "- immagine 2: C2, pagina 4" in p
    assert prepared.not_studied == ["beta", "gamma", "delta"]
    assert "Non ci sono immagini allegate" in prepare(app, docs, "", with_images=False).prompt
    with pytest.raises(GroupingError, match="almeno 2"):
        prepare(app, docs[:1], "", False)
    schema = grouping_schema(["C1", "C2"])
    assert schema["properties"]["ungrouped"]["items"]["properties"]["catalog"]["enum"] == ["C1", "C2"]


def test_validate_fixes_content_and_rejects_structure():
    codes = {"C1": "alfa", "C2": "beta", "C3": "gamma", "C4": "delta"}
    groups, ungrouped, notes, problems = validate(ANSWER, codes)
    assert [(g.name, g.documents) for g in groups] == [("Motori diesel", ["alfa", "beta"])]
    assert groups[0].common_tags == ["motori", "diesel"]
    assert [f["name"] for f in groups[0].fields] == ["motore", "codice FIN"]
    # gruppo di uno e catalogo dimenticato: tra i non accorpabili, segnalati
    assert [(u.document, u.reason) for u in ungrouped] == [
        ("gamma", "da solo"), ("delta", "non classificato da Gemini")]
    assert len(problems) == 2 and notes == "C4 era poco descritto."

    def broken(change):
        answer = copy.deepcopy(ANSWER)
        change(answer)
        return answer

    cases = [
        (broken(lambda a: a.pop("notes")), "notes: campo mancante"),
        (broken(lambda a: a.update(extra=1)), "extra: campo non previsto"),
        (broken(lambda a: a["groups"][0].update(catalogs="C1, C2")), "deve essere un elenco"),
        (broken(lambda a: a["groups"][0]["catalogs"].append("C9")), "sconosciuto 'C9'"),
        (broken(lambda a: a["groups"][0]["catalogs"].append("alfa")), "sconosciuto 'alfa'"),
        (broken(lambda a: a["ungrouped"].append({"catalog": "C1", "reason": ""})), "compare anche"),
        (broken(lambda a: a["groups"][0].pop("fields")), "fields: campo mancante"),
        ("testo libero", "oggetto JSON"),
    ]
    for answer, expected in cases:
        with pytest.raises(InvalidAnswerError, match=expected):
            validate(answer, codes)


def test_run_archive_and_move(setup):
    app, docs, responder = setup
    g = run_grouping(app, app.client(), docs, "", with_images=True)
    request = responder.requests[0]
    assert request.kind == "json" and len(request.images) == 2
    assert request.json_schema["properties"]["groups"]["items"]["properties"]["catalogs"]["items"]["enum"][0] == "C1"
    assert list_groupings(app.workspace)[0].id == g.id and g.status == "valida"

    move_document(g, "gamma", "0")
    assert g.groups[0].documents == ["alfa", "beta", "gamma"] and "gamma" not in [u.document for u in g.ungrouped]
    move_document(g, "delta", "new")
    assert g.groups[1].documents == ["delta"]
    move_document(g, "delta", "ungrouped")
    assert len(g.groups) == 1          # il gruppo rimasto vuoto sparisce
    # tornando tra i non accorpabili riprende il motivo originale di Gemini
    assert [u.reason for u in g.ungrouped if u.document == "delta"] == ["non classificato da Gemini"]
    with pytest.raises(GroupingError, match="non presente"):
        move_document(g, "inesistente", "ungrouped")

    responder.answer = {"groups": []}
    with pytest.raises(InvalidAnswerError):
        run_grouping(app, app.client(), docs, "", with_images=False)
    stored = list_groupings(app.workspace)[0]
    assert stored.status == "non_valida" and stored.raw == {"groups": []}
    with pytest.raises(GroupingError, match="non è valido"):
        move_document(stored, "alfa", "ungrouped")


def test_api_flow(setup):
    app, docs, responder = setup
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    ids = [d.id for d in docs]
    prev = client.post("/api/library/groupings/preview", json={"ids": ids, "with_images": True}).json()
    assert prev["catalogs"] == 4 and len(prev["images"]) == 2 and prev["not_studied"] == ["beta", "gamma", "delta"]
    assert responder.requests == []                     # l'anteprima non chiama Gemini
    assert client.post("/api/library/groupings/preview", json={"ids": ids[:1]}).status_code == 400

    job = client.post("/api/library/groupings", json={"ids": ids, "instructions": "X"}).json()
    assert job["status"] == "completato" and job["kind"] == "grouping"
    gid = job["result"]["grouping_id"]
    listed = client.get("/api/library/groupings").json()
    assert listed[0]["id"] == gid and listed[0]["groups"] == 1 and listed[0]["ungrouped"] == 2

    view = client.get(f"/api/library/groupings/{gid}").json()
    assert view["catalogs"]["alfa"]["title"] == "Alfa" and view["catalogs"]["alfa"]["studied"] is True
    body = {"name": "Motori", "tags": ["diesel", "fin"]}
    view = client.put(f"/api/library/groupings/{gid}/groups/0", json=body).json()
    assert view["groups"][0]["name"] == "Motori" and view["groups"][0]["common_tags"] == ["diesel", "fin"]

    view = client.post(f"/api/library/groupings/{gid}/groups/0/tags", json={"tags": ["diesel", "fin"]}).json()
    assert view["groups"][0]["tags_applied"] == ["diesel", "fin"]
    assert app.library.card(docs[1]).tags == ["diesel", "fin"]
    assert client.post(f"/api/library/groupings/{gid}/groups/0/tags", json={"tags": []}).status_code == 400

    view = client.post(f"/api/library/groupings/{gid}/groups/0/project", json={"name": "Motori diesel"}).json()
    project = view["groups"][0]["project"]
    assert project["name"] == "Motori diesel"
    assert client.post(f"/api/library/groupings/{gid}/groups/0/project", json={}).status_code == 400
    # il progetto ha i cataloghi del gruppo e le colonne unificate come obiettivo proposto
    summary = client.get(f"/api/projects/{project['id']}").json()
    assert [d["id"] for d in summary["documents"]] == ["alfa", "beta"]
    setup_view = client.get(f"/api/projects/{project['id']}/setup").json()
    assert "- motore: modello\n- codice FIN" in setup_view["suggested_goal"]
    # gruppo con progetto creato: i suoi cataloghi non si spostano più da qui
    move = {"document": "alfa", "to": "ungrouped"}
    assert client.post(f"/api/library/groupings/{gid}/move", json=move).status_code == 400
    view = client.post(f"/api/library/groupings/{gid}/move", json={"document": "gamma", "to": "new"}).json()
    assert view["groups"][1]["documents"] == ["gamma"]

    client.put("/api/library/grouping-prompt", json={"text": "Mie"})
    assert client.get("/api/library/grouping-prompt").json()["effective"] == "Mie"

    responder.answer = {"groups": [], "ungrouped": [], "notes": "", "extra": True}
    job = client.post("/api/library/groupings", json={"ids": ids}).json()
    assert job["status"] == ERROR and "non rispetta il formato" in job["error"]
    assert client.get("/api/library/groupings").json()[0]["status"] == "non_valida"
