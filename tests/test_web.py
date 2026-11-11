"""API dell'interfaccia grafica, organizzate per progetto (Gemini simulato, lavori subito)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_setup_session import SPEC_V2, FakeCatalog, answer

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.page_runner import RunControl, run_pages
from ragpdf.web.jobs import CANCELLED, DONE, ERROR, JobConflictError, JobManager
from ragpdf.web.server import create_app


@pytest.fixture
def web(config):
    fake = FakeCatalog()
    app = App(config, client=FakeGeminiClient(fake, parallelism=2))
    client = TestClient(create_app(app, JobManager(run_inline=True)))
    return client, fake, app


def new_project(client, name, *pdfs):
    project = client.post("/api/projects", json={"name": name}).json()
    for pdf in pdfs:
        with open(pdf, "rb") as f:
            url = f"/api/projects/{project['id']}/documents"
            r = client.post(url, files={"file": (pdf.name, f, "application/pdf")})
            assert r.status_code == 200, r.text
    return project["id"]


def test_page_and_status(web):
    client, _, _ = web
    assert "ragpdf" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert "GeminiSnake" in client.get("/static/snake.js").text
    assert "/static/snake.js" in client.get("/").text
    assert client.get("/api/status").json()["model"]
    assert client.post("/api/projects", json={"name": " "}).status_code == 400


def test_projects_and_documents(web, pdf_factory):
    client, _, _ = web
    a, b = pdf_factory("Alfa.pdf", pages=4, prefix="A"), pdf_factory("Beta.pdf", pages=3, prefix="B")
    pid = new_project(client, "Motori Diesel", a, b)
    assert pid == "motori-diesel"
    project = client.get(f"/api/projects/{pid}").json()
    assert [d["id"] for d in project["documents"]] == ["alfa", "beta"] and project["page_count"] == 7
    # stesso PDF in un altro progetto: riusato, non duplicato
    other = client.post("/api/projects", json={"name": "Altro"}).json()["id"]
    with open(a, "rb") as f:
        r = client.post(f"/api/projects/{other}/documents", files={"file": ("Alfa.pdf", f, "application/pdf")}).json()
    assert r["already_present"] and r["id"] == "alfa"
    bad = client.post(f"/api/projects/{pid}/documents", files={"file": ("nota.txt", b"x", "text/plain")})
    assert bad.status_code == 400
    assert client.get("/api/documents/alfa/thumb/2").headers["content-type"] == "image/png"
    assert client.get("/api/documents/alfa/page/9").status_code == 404
    assert client.get("/api/documents/alfa/pdf").headers["content-type"] == "application/pdf"
    assert client.get("/api/projects/nessuno").status_code == 404

    # impostazioni per PDF
    s = client.put(f"/api/projects/{pid}/documents/beta/settings", json={"notes": "nota B", "pages": "2-"}).json()
    assert s["settings"] == {"notes": "nota B", "pages": "2-"}
    assert client.put(f"/api/projects/{pid}/documents/beta/settings", json={"pages": "9"}).status_code == 400

    # togliere: 'alfa' resta (è anche in 'altro'), 'beta' viene eliminato
    assert client.delete(f"/api/projects/{pid}/documents/alfa").json()["deleted"] is False
    assert client.delete(f"/api/projects/{pid}/documents/beta").json()["deleted"] is True
    assert client.get(f"/api/projects/{pid}").json()["documents"] == []
    assert client.delete(f"/api/projects/{other}").json()["documents_deleted"] == ["alfa"]


def test_full_project_flow(web, pdf_factory, tmp_path):
    client, fake, app = web
    pid = new_project(client, "Flusso", pdf_factory("Alfa.pdf", pages=6, prefix="A"),
                      pdf_factory("Beta.pdf", pages=4, prefix="B"))
    base = f"/api/projects/{pid}"

    data = client.get(f"{base}/setup").json()
    assert data["session"] is None and ["beta", 2] in data["suggested_pages"]

    ref = tmp_path / "rif.csv"
    ref.write_text("MOTORE,CODICE,Pagina,File\nM3,C3,3,Alfa.pdf\nM2,C2,2,Beta.pdf\n", encoding="utf-8")
    with open(ref, "rb") as f:
        reference = client.post("/api/references", files={"file": ("rif.csv", f, "text/csv")}).json()

    view = client.post(f"{base}/setup", json={"context_pages": [["alfa", 1], ["alfa", 3], ["beta", 2]],
                                              "goal": "una riga per codice", "reference": reference["path"]}).json()
    assert view["round"] is None and view["state"]["context_pages_text"] == "Alfa: 1, 3; Beta: 2"
    assert not fake.setup_prompts
    prompt = client.get(f"{base}/setup/prompt?kind=initial").json()
    assert "pagina 3 dell'estratto = Beta [beta], pagina 2" in prompt["prompt"]
    assert client.post(f"{base}/setup", json={"context_pages": [["gamma", 1]]}).status_code == 400

    fake.setup_answers = [answer(pages=(2, 3), notes=[("[beta]", "In Beta il motore è in basso.")])]
    job = client.post(f"{base}/setup/start").json()
    assert job["status"] == DONE, job
    rnd = client.get(f"{base}/setup").json()["session"]["round"]
    assert rnd["document_notes"] == {"beta": "In Beta il motore è in basso."}
    assert {r["File"] for r in rnd["preview"]["rows"]} == {"Alfa.pdf", "Beta.pdf"}
    assert [x["label"] for x in rnd["preview"]["rows_per_page"]] == ["Alfa p.3", "Beta p.2"]
    assert rnd["preview"]["comparison"]["recall"] == 1.0

    # prova su altre pagine, scritte a testo
    assert client.post(f"{base}/setup/preview", json={"pages": "alfa: 5"}).json()["status"] == DONE
    view = client.get(f"{base}/setup").json()["session"]
    assert view["state"]["preview_pages"] == [["alfa", 5], ["beta", 2]]          # beta aggiunta d'ufficio
    # note modificate a mano
    view = client.put(f"{base}/setup/notes", json={"notes": {"alfa": "nota A", "beta": " ", "gamma": "x"}}).json()
    assert view["round"]["document_notes"] == {"alfa": "nota A"}

    fake.setup_answers = [answer(spec=SPEC_V2, pages=())]
    assert client.post(f"{base}/setup/revise", json={"feedback": "descrivi il codice"}).json()["status"] == DONE
    saved = client.post(f"{base}/setup/save", json={}).json()
    assert saved["spec_name"] == pid and saved["reused_pages"] == 2
    project = client.get(base).json()
    assert project["spec"] == pid and project["documents"][0]["settings"]["notes"] == "nota A"

    plan = client.get(f"{base}/index/plan").json()
    assert plan["pages"] == 10 and plan["done"] == 2 and plan["todo"] == 8 and not plan["spec_changed"]
    job = client.post(f"{base}/index", json={}).json()
    assert job["status"] == DONE and job["result"]["documents"]["alfa"]["succeeded"] == 5
    assert job["done"] == job["total"] == 8
    res = client.get(f"{base}/results").json()
    assert res["available"] and res["stats"]["righe_finali"] == 10 and res["stats"]["documenti_indicizzati"] == 2
    csv = client.get(f"{base}/download/csv").content.decode("utf-8-sig")
    assert "Alfa.pdf" in csv and "Beta.pdf" in csv
    assert client.get(f"{base}/download/documento?doc=beta").status_code == 200
    search = client.get(f"{base}/search")
    assert search.status_code == 200 and "/api/documents/{doc}/pdf#page={page}" in search.text

    # note cambiate per un PDF: solo quello va rifatto
    client.put(f"{base}/documents/beta/settings", json={"notes": "nuova nota"})
    plan = client.get(f"{base}/index/plan").json()
    assert plan["spec_changed"] == ["beta"] and plan["todo"] == 4
    assert client.post(f"{base}/index", json={}).status_code == 409
    job = client.post(f"{base}/index", json={"force": True}).json()
    assert job["result"]["documents"]["beta"]["succeeded"] == 4
    assert job["result"]["documents"]["alfa"]["succeeded"] == 0                  # alfa non rifatto


def test_save_conflicts_and_start_from_existing(web, pdf_factory):
    client, fake, app = web
    uno = new_project(client, "Uno", pdf_factory("Uno.pdf", pages=4))
    due = new_project(client, "Due", pdf_factory("Due.pdf", pages=4, prefix="D"))
    fake.setup_answers = [answer(pages=(2,))]
    client.post(f"/api/projects/{uno}/setup", json={"context_pages": "1-2"})
    client.post(f"/api/projects/{uno}/setup/start")
    client.post(f"/api/projects/{uno}/setup/save", json={})

    fake.setup_answers = [answer(spec=SPEC_V2, pages=(2,))]
    client.post(f"/api/projects/{due}/setup", json={"context_pages": "1-2", "name": "uno"})
    client.post(f"/api/projects/{due}/setup/start")
    conflict = client.post(f"/api/projects/{due}/setup/save", json={})
    assert conflict.status_code == 409 and "uno.yaml" in conflict.json()["conflicts"]
    assert client.post(f"/api/projects/{due}/setup/save", json={"name": "Due Bis"}).json()["spec_name"] == "due-bis"

    calls = len(fake.setup_prompts)
    view = client.post(f"/api/projects/{due}/setup", json={"from_primer": "uno", "from_spec": "uno"}).json()
    assert view["round"]["number"] == 1 and len(fake.setup_prompts) == calls
    assert client.post(f"/api/projects/{due}/setup", json={"from_primer": "uno"}).status_code == 400


def test_index_requires_configuration(web, pdf_factory):
    client, _, _ = web
    pid = new_project(client, "Nuovo", pdf_factory("Nuovo.pdf", pages=2))
    r = client.get(f"/api/projects/{pid}/index/plan")
    assert r.status_code == 400 and "configurazione" in r.json()["detail"]


def test_gemini_error_inside_job_is_reported(web, pdf_factory):
    client, fake, _ = web
    pid = new_project(client, "Errore", pdf_factory("Errore.pdf", pages=3))
    client.post(f"/api/projects/{pid}/setup", json={"context_pages": "1"})
    fake.setup_answers = [answer(spec="row: x"), answer(spec="row: x")]
    job = client.post(f"/api/projects/{pid}/setup/start").json()
    assert job["status"] == ERROR and "due volte" in job["error"]
    assert client.get(f"/api/projects/{pid}/setup").json()["session"]["round"] is None


def test_job_manager_one_job_per_project_and_cancel():
    jobs = JobManager()
    release = threading.Event()

    def slow(job):
        release.wait(5)
        return "fatto"

    first = jobs.submit("p", "x", "lavoro lento", slow)
    with pytest.raises(JobConflictError):
        jobs.submit("p", "y", "altro", slow)
    other = jobs.submit("altro", "y", "altro", lambda job: 1)
    release.set()
    for _ in range(100):
        if first.status == DONE and other.status == DONE:
            break
        time.sleep(0.02)
    assert first.result == "fatto" and other.result == 1

    class _Manifest:
        def mark_done(self, n): pass
        def mark_failed(self, n, e): pass

    seen = []
    jobs2 = JobManager()

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


def test_run_control_reports_progress():
    events = []
    control = RunControl(progress=lambda d, t, m: events.append((d, t)))

    class _Manifest:
        def mark_done(self, n): pass
        def mark_failed(self, n, e): pass

    run_pages([1, 2, 3], lambda n: None, _Manifest(), 2, "x", False, control)
    assert events[0] == (0, 3) and events[-1] == (3, 3)


def test_job_shows_service_messages_and_can_be_cancelled_during_overload(config, pdf_factory):
    """Gemini sovraccarico durante l'analisi: i messaggi arrivano alla pagina e 'Ferma' interrompe subito."""
    from google.genai import errors

    from ragpdf.core.config import GeminiSettings
    from ragpdf.core.gemini import GeminiClient

    class Models:
        calls = 0

        def generate_content(self, model, contents, config):
            Models.calls += 1
            raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand"}})

    from types import SimpleNamespace as NS

    app = App(config)
    app._client = GeminiClient(["k"], GeminiSettings(retry_delay_seconds=1, requests_per_minute_per_key=600),
                               client_factory=lambda k: NS(models=Models()), log=app.emit)
    jobs = JobManager()
    client = TestClient(create_app(app, jobs))
    pid = new_project(client, "Lento", pdf_factory("Lento.pdf", pages=2))
    client.post(f"/api/projects/{pid}/setup", json={"context_pages": "1"})
    job = client.post(f"/api/projects/{pid}/setup/start").json()
    for _ in range(100):                      # aspetta i primi messaggi di sovraccarico
        data = client.get(f"/api/jobs/{job['id']}").json()
        if len(data["events"]) >= 2:
            break
        time.sleep(0.05)
    assert "sovraccarico" in data["events"][-1]["text"] and data["events"][-1]["time"]
    start = time.time()
    client.post(f"/api/jobs/{job['id']}/cancel")
    for _ in range(100):
        data = client.get(f"/api/jobs/{job['id']}").json()
        if data["status"] != "in_corso":
            break
        time.sleep(0.05)
    assert data["status"] == CANCELLED and time.time() - start < 3
    assert client.get(f"/api/projects/{pid}/setup").json()["session"]["round"] is None
