from ragpdf.core.fake_gemini import FakeGeminiClient
from ragpdf.indexing.spec import load_spec
from ragpdf.indexing.spec_draft import DraftInput, default_sample_pages, draft_spec

VALID = """```yaml
name: qualsiasi
row: Una riga per codice.
columns:
  - name: CODICE
    description: codice articolo
  - name: MARCA
    context: true
output:
  page_column: Pagina
  source_column: File
```"""


def test_default_sample_pages():
    assert default_sample_pages(3) == [1, 2, 3]
    assert default_sample_pages(100) == [49, 50, 51, 52]


def test_draft_with_retry_example_and_pages(tmp_path, store, pdf_factory):
    doc = store.add(pdf_factory(pages=10))
    example = tmp_path / "indice.csv"
    example.write_text("CODICE,MARCA,Pagina,File\nA1,X,3,c.pdf\n", encoding="utf-8")
    answers = iter(["questo: non: è: yaml: [", VALID])
    client = FakeGeminiClient(lambda req: next(answers))

    target = store.workspace.spec_path("ricambi")
    spec = draft_spec(DraftInput("ricambi", "una riga per codice", example, doc, "PRIMER"), client, target)

    assert spec.name == "ricambi"
    assert [c.name for c in spec.columns] == ["CODICE", "MARCA"]
    assert spec.example_csv_path() == target.with_name("ricambi_esempio.csv")
    assert load_spec(target).examples.csv == "ricambi_esempio.csv"

    first, second = client.requests
    assert "una riga per codice" in first.prompt and "A1,X,3,c.pdf" in first.prompt and "PRIMER" in first.prompt
    assert len(first.images) == 4
    assert "NON ERA VALIDA" in second.prompt
