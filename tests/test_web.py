"""API dell'interfaccia grafica (Gemini simulato, lavori eseguiti subito)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_setup_session import SPEC_V1, SPEC_V2, FakeCatalog, answer

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.page_runner import RunControl, run_pages
from ragpdf.web.jobs import CANCELLED, DONE, ERROR, JobConflictError, JobManager
from ragpdf.web.server import create_app


@pytest.fixture
def web(config, tmp_path):
    fake = FakeCatalog()
    app = App(config, client=FakeGeminiClient(fake, parallelism=2))
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    return client, fake, app


def upload(client, path):
    with open(path, "rb") as f:
        return client.post("/api/documents", files={"file": (path.name, f, "application/pdf")})


def test_page_and_status(web):
    client, _, _ = web
    assert "ragpdf" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    status = client.get("/api/status").json()
    assert status["model"] and "usage" in status


def test_upload_and_documents(web, pdf_factory):
    client, _, _ = web
    pdf = pdf_factory("Catalogo Web.pdf", pages=5)
    r = upload(client, pdf)
    assert r.status_code == 200 and r.json()["id"] == "catalogo-web" and r.json()["scanned"] is False
    dup = upload(client, pdf)
    assert dup.status_code == 409 and dup.json()["existing"] == "catalogo-web"
    assert [d["id"] for d in client.get("/api/documents").json()] == ["catalogo-web"]
    thumb = client.get("/api/documents/catalogo-web/thumb/2")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/png"
    assert client.get("/api/documents/catalogo-web/page/2").status_code == 200
    assert client.get("/api/documents/catalogo-web/page/99").status_code == 404
    assert client.get("/api/documents/nessuno").status_code == 404
    bad = client.post("/api/documents", files={"file": ("nota.txt", b"ciao", "text/plain")})
    assert bad.status_code == 400


def test_full_guided_flow(web, pdf_factory, tmp_path):
    client, fake, app = web
    upload(client, pdf_factory("Flusso.pdf", pages=8))
    base = "/api/documents/flusso"

    data = client.get(f"{base}/setup").json()
    assert data["session"] is None and data["suggested_pages"]

    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE,Pagina,File\nM3,C3,3,Flusso.pdf\n", encoding="utf-8")
    with open(ref, "rb") as f:
        reference = client.post("/api/references", files={"file": ("rif.csv", f, "text/csv")}).json()
    assert reference["columns"] == ["MOTORE", "CODICE", "Pagina", "File"] and reference["rows"] == 1

    # nuova sessione: nessuna chiamata a Gemini finché non si preme "Invia"
    view = client.post(f"{base}/setup", json={"context_pages": "1, 3-4", "goal": "una riga per codice",
                                               "reference": reference["path"]}).json()
    assert view["round"] is None and view["state"]["context_pages"] == [1, 3, 4]
    assert not fake.setup_prompts
    prompt = client.get(f"{base}/setup/prompt?kind=initial").json()
    assert "pagina 2 dell'estratto = pagina 3" in prompt["prompt"] and "3 pagine" in prompt["attachment"]

    fake.setup_answers = [answer(pages=(3,))]
    job = client.post(f"{base}/setup/start").json()
    assert job["status"] == DONE, job
    view = client.get(f"{base}/setup").json()["session"]
    rnd = view["round"]
    assert rnd["number"] == 1 and rnd["spec"]["valid"] and rnd["spec"]["page_column"] == "Pagina"
    assert rnd["preview"]["rows"][0]["CODICE"] == "C3"
    assert rnd["preview"]["comparison"]["recall"] == 1.0
    assert client.post(f"{base}/setup/start").status_code == 400      # già avviata

    revise_prompt = client.get(f"{base}/setup/prompt", params={"kind": "revise", "feedback": "TUTTO MAIUSCOLO"}).json()
    assert "TUTTO MAIUSCOLO" in revise_prompt["prompt"]
    assert client.post(f"{base}/setup/revise", json={"feedback": "  "}).status_code == 400
    fake.setup_answers = [answer(spec=SPEC_V2, pages=(4,))]
    assert client.post(f"{base}/setup/revise", json={"feedback": "descrivi il codice"}).json()["status"] == DONE
    view = client.get(f"{base}/setup").json()["session"]
    assert view["round"]["number"] == 2 and len(view["rounds"]) == 2

    # versioni, modifiche manuali, prova su altre pagine
    assert client.post(f"{base}/setup/round/1").json()["round"]["number"] == 1
    assert client.post(f"{base}/setup/round/9").status_code == 400
    bad = client.put(f"{base}/setup/spec", json={"text": "row: x\ncolumns: []\n"})
    assert bad.status_code == 400
    assert client.put(f"{base}/setup/primer", json={"text": "# Primer a mano"}).json()["round"]["number"] == 3
    assert client.post(f"{base}/setup/preview", json={"pages": "5-6"}).json()["status"] == DONE
    view = client.get(f"{base}/setup").json()["session"]
    assert view["state"]["preview_pages"] == [5, 6] and view["round"]["preview"]["pages"] == [5, 6]
    assert client.get(f"{base}/setup/preview.csv").status_code == 200

    # salvataggio
    saved = client.post(f"{base}/setup/save", json={}).json()
    assert saved["spec_name"] == "flusso" and saved["reused_pages"] == 2
    doc = client.get(base).json()
    assert doc["primer"] == "flusso" and doc["spec"] == "flusso"

    # indicizzazione completa
    plan = client.get(f"{base}/index/plan").json()
    assert (plan["pages"], plan["done"], plan["todo"], plan["spec_changed"]) == (8, 2, 6, False)
    job = client.post(f"{base}/index", json={}).json()
    assert job["status"] == DONE and job["result"]["succeeded"] == 6
    assert client.get(f"{base}/index/plan").json()["todo"] == 0
    results = client.get(f"{base}/results").json()
    assert results["available"] and results["stats"]["righe_finali"] == 8 and not results["stale"]
    csv = client.get(f"{base}/download/csv")
    assert csv.status_code == 200 and "C8" in csv.content.decode("utf-8-sig")

    # cambiare le regole dopo l'indicizzazione: serve rifare da capo
    client.put(f"{base}/setup/primer", json={"text": "# Primer cambiato"})
    client.post(f"{base}/setup/save", json={"overwrite": True})
    assert client.get(f"{base}/index/plan").json()["spec_changed"] is True
    assert client.get(f"{base}/results").json()["stale"] is True
    refused = client.post(f"{base}/index", json={})
    assert refused.status_code == 409 and refused.json()["spec_changed"]
    assert client.post(f"{base}/index", json={"force": True}).json()["result"]["succeeded"] == 8


def test_save_conflicts_and_start_from_existing(web, pdf_factory):
    client, fake, app = web
    upload(client, pdf_factory("Uno.pdf", pages=4))
    upload(client, pdf_factory("Due.pdf", pages=4, prefix="D"))
    fake.setup_answers = [answer(pages=(2,))]
    client.post("/api/documents/uno/setup", json={"context_pages": "1-2"})
    client.post("/api/documents/uno/setup/start")
    client.post("/api/documents/uno/setup/save", json={})

    # stesso nome, contenuto diverso: 409 con l'elenco dei file
    fake.setup_answers = [answer(spec=SPEC_V2, pages=(2,))]
    client.post("/api/documents/due/setup", json={"context_pages": "1-2", "name": "uno"})
    client.post("/api/documents/due/setup/start")
    conflict = client.post("/api/documents/due/setup/save", json={})
    assert conflict.status_code == 409 and "uno.yaml" in conflict.json()["conflicts"]
    assert client.post("/api/documents/due/setup/save", json={"name": "Due Bis"}).json()["spec_name"] == "due-bis"

    # partire da file esistenti: subito una versione, senza Gemini
    calls = len(fake.setup_prompts)
    view = client.post("/api/documents/due/setup", json={"from_primer": "uno", "from_spec": "uno"}).json()
    assert view["round"]["number"] == 1 and len(fake.setup_prompts) == calls
    assert client.post("/api/documents/due/setup", json={"from_primer": "uno"}).status_code == 400
    assert client.get("/api/library").json() == {"primers": ["due-bis", "uno"], "specs": ["due-bis", "uno"]}


def test_index_requires_configuration(web, pdf_factory):
    client, _, _ = web
    upload(client, pdf_factory("Nuovo.pdf", pages=2))
    r = client.get("/api/documents/nuovo/index/plan")
    assert r.status_code == 400 and "configurazione" in r.json()["detail"]


def test_gemini_error_inside_job_is_reported(web, pdf_factory):
    client, fake, _ = web
    upload(client, pdf_factory("Errore.pdf", pages=3))
    client.post("/api/documents/errore/setup", json={"context_pages": "1"})
    fake.setup_answers = [answer(spec="row: x"), answer(spec="row: x")]       # due specifiche non valide
    job = client.post("/api/documents/errore/setup/start").json()
    assert job["status"] == ERROR and "due volte" in job["error"]
    assert client.get("/api/documents/errore/setup").json()["session"]["round"] is None


def test_delete_document(web, pdf_factory):
    client, _, _ = web
    upload(client, pdf_factory("Via.pdf", pages=1))
    assert client.delete("/api/documents/via").json() == {"deleted": "via"}
    assert client.get("/api/documents").json() == []


def test_job_manager_one_job_per_document_and_cancel():
    jobs = JobManager()
    release = threading.Event()

    def slow(job):
        release.wait(5)
        return "fatto"

    first = jobs.submit("doc", "x", "lavoro lento", slow)
    with pytest.raises(JobConflictError):
        jobs.submit("doc", "y", "altro", slow)
    other = jobs.submit("altro-doc", "y", "altro", lambda job: 1)
    release.set()
    for _ in range(100):
        if first.status == DONE and other.status == DONE:
            break
        time.sleep(0.02)
    assert first.result == "fatto" and other.result == 1

    # annullamento: le pagine non ancora partite non vengono elaborate
    class _Manifest:
        def mark_done(self, n): pass
        def mark_failed(self, n, e): pass

    seen = []
    jobs2 = JobManager(run_inline=False)

    def work(job):
        def worker(n):
            seen.append(n)
            if n == 2:
                jobs2.cancel(job.id)
        return run_pages(list(range(1, 50)), worker, _Manifest(), 1, "prova", False, job.control)

    job = jobs2.submit("d", "index", "indicizzazione", work)
    for _ in range(200):
        if job.status != "in_corso":
            break
        time.sleep(0.02)
    assert job.status == CANCELLED
    assert len(seen) < 49 and job.result.stopped_reason == "annullato dall'utente"
    assert job.done == job.total == 49


def test_run_control_reports_progress():
    events = []
    control = RunControl(progress=lambda d, t, m: events.append((d, t)))

    class _Manifest:
        def mark_done(self, n): pass
        def mark_failed(self, n, e): pass

    run_pages([1, 2, 3], lambda n: None, _Manifest(), 2, "x", False, control)
    assert events[0] == (0, 3) and events[-1] == (3, 3)


def test_spec_v1_is_still_valid():
    assert "columns" in SPEC_V1
