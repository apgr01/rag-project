"""Indicizzazione completa con Gemini simulato."""

import re

import pytest

from ragpdf.app import App
from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.core.gemini import QuotaExhaustedError
from ragpdf.indexing.extractor import SpecChangedError
from ragpdf.indexing.spec import IndexingSpec

SPEC = IndexingSpec.model_validate(
    {
        "name": "ricambi",
        "row": "un codice articolo",
        "columns": [
            {"name": "MOTORE", "context": True},
            {"name": "CODICE", "required": True},
        ],
    }
)


def page_of(prompt: str) -> int:
    return int(re.search(r"pagina (\d+) di", prompt).group(1))


def catalog_responder(request):
    """Simula un catalogo: pagina 1 copertina, poi due codici per pagina.
    L'intestazione MOTORE compare solo sulle pagine dispari."""
    n = page_of(request.prompt)
    if n == 1:
        return {"page_summary": "copertina", "page_context": {"motore": ""}, "rows": [], "notes": ""}
    context = {"motore": f"MOT-{n}"} if n % 2 else {"motore": ""}
    rows = [{"motore": "", "codice": f"C{n}-{i}"} for i in (1, 2)]
    return {"page_summary": "tabella", "page_context": context, "rows": rows, "notes": ""}


@pytest.fixture
def app(config):
    return App(config, client=FakeGeminiClient(catalog_responder, parallelism=3))


def test_full_indexing_and_export(app, pdf_factory):
    doc = app.documents.add(pdf_factory(pages=4))
    report = app.extractor().run(doc, SPEC, "primer", show_progress=False)
    assert report.succeeded == 4 and report.completed

    result = app.export(doc, SPEC)
    text = result.csv_path.read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "MOTORE,CODICE,PAGINA_PDF,FILE_ORIGINE"
    assert lines[1] == "MOT-3,C2-1,2,Catalogo Prova.pdf" or lines[1].startswith(",C2-1")
    # pagina 2 non ha intestazione e nessuna pagina precedente con motore: cella vuota
    # pagina 4 eredita il motore della pagina 3
    assert "MOT-3,C4-1,4,Catalogo Prova.pdf" in lines
    assert result.index.stats["righe_finali"] == 6
    # MOTORE vuoto non è un errore (non obbligatorio): nessuna anomalia
    assert result.anomalies_path is None

    # rilancio: niente da fare, nessuna nuova richiesta
    before = len(app.client().requests)
    assert app.extractor().run(doc, SPEC, "primer", show_progress=False).requested == 0
    assert len(app.client().requests) == before


def test_prompt_contains_primer_and_image(app, pdf_factory):
    doc = app.documents.add(pdf_factory(pages=2))
    app.extractor().run(doc, SPEC, "IL MIO PRIMER", pages="2", show_progress=False)
    (req,) = app.client().requests
    assert "IL MIO PRIMER" in req.prompt and req.kind == "json"
    assert req.images[0].name == "page-0002.png"


def test_failed_pages_are_retried_on_next_run(app, pdf_factory):
    doc = app.documents.add(pdf_factory(pages=3))
    flaky = {"fail": True}

    def responder(request):
        if page_of(request.prompt) == 2 and flaky["fail"]:
            raise RuntimeError("risposta illeggibile")
        return catalog_responder(request)

    app.client().responder = responder
    report = app.extractor().run(doc, SPEC, "p", show_progress=False)
    assert list(report.failed) == [2]
    assert any(a.page == 2 and a.level == "errore" for a in app.export(doc, SPEC).index.anomalies)

    flaky["fail"] = False
    report = app.extractor().run(doc, SPEC, "p", show_progress=False)
    assert report.succeeded == 1 and report.completed


def test_quota_exhausted_stops_cleanly(app, pdf_factory):
    doc = app.documents.add(pdf_factory(pages=5))

    def responder(request):
        if page_of(request.prompt) >= 3:
            raise QuotaExhaustedError("quote finite")
        return catalog_responder(request)

    app.client().responder = responder
    report = app.extractor().run(doc, SPEC, "p", pages="1-2", show_progress=False)
    assert report.completed
    report = app.extractor().run(doc, SPEC, "p", show_progress=False)
    assert report.stopped_reason == "quote finite"
    assert not report.failed  # la quota non è colpa delle pagine: restano da fare


def test_spec_change_is_detected(app, pdf_factory):
    doc = app.documents.add(pdf_factory(pages=2))
    app.extractor().run(doc, SPEC, "primer", show_progress=False)
    changed = SPEC.model_copy(update={"instructions": "nuova regola"})
    with pytest.raises(SpecChangedError):
        app.extractor().run(doc, changed, "primer", show_progress=False)
    with pytest.raises(SpecChangedError):
        app.extractor().run(doc, SPEC, "primer modificato", show_progress=False)
    report = app.extractor().run(doc, changed, "primer", force=True, show_progress=False)
    assert report.succeeded == 2


def test_malformed_model_output_is_tolerated(app, pdf_factory):
    doc = app.documents.add(pdf_factory(pages=1))
    app.client().responder = lambda r: {"rows": [{"codice": 123}, "spazzatura", {"motore": None, "codice": "X"}]}
    app.extractor().run(doc, SPEC, "", show_progress=False)
    rows = app.export(doc, SPEC).index.rows
    assert [r["CODICE"] for r in rows] == ["123", "X"]
