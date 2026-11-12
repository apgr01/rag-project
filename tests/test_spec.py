from pathlib import Path

import pytest

from ragpdf.indexing.prompt import PromptBuilder, build_page_schema
from ragpdf.indexing.spec import SpecError, load_spec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "specs"


@pytest.mark.parametrize("name", ["base", "finimpianti-fin-oem"])
def test_example_specs_are_valid(name):
    spec = load_spec(EXAMPLES / f"{name}.yaml")
    schema = build_page_schema(spec)
    assert schema["properties"]["rows"]["items"]["required"] == list(spec.json_keys().values())
    prompt = PromptBuilder(spec, "PRIMER DI PROVA", total_pages=10).page_prompt(3)
    assert "PRIMER DI PROVA" in prompt and "pagina 3 di 10" in prompt
    assert "{{" not in prompt


def test_finimpianti_mapping_keeps_tricky_yaml_keys():
    spec = load_spec(EXAMPLES / "finimpianti-fin-oem.yaml")
    values = spec.mappings["componenti"].values
    assert values["NO"] == "Ugello / Nozzle"   # "NO" non deve diventare False
    assert spec.json_keys()["NR. CILINDRI"] == "nr_cilindri"


def write(tmp_path, text):
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_spec_errors_are_readable(tmp_path):
    with pytest.raises(SpecError, match="row"):
        load_spec(write(tmp_path, "columns:\n  - name: A\n"))
    with pytest.raises(SpecError, match="duplicate"):
        load_spec(write(tmp_path, "row: x\ncolumns:\n  - name: A\n  - name: A\n"))
    with pytest.raises(SpecError, match="colum"):
        load_spec(write(tmp_path, "row: x\ncolums:\n  - name: A\n"))   # chiave sbagliata
    with pytest.raises(SpecError, match="regolare"):
        load_spec(write(tmp_path, "row: x\ncolumns:\n  - name: A\n    pattern: '['\n"))
    with pytest.raises(SpecError, match="sort_by"):
        load_spec(write(tmp_path, "row: x\ncolumns:\n  - name: A\noutput:\n  sort_by: [B]\n"))


def test_fingerprint_changes_only_when_extraction_changes(tmp_path):
    base = "row: x\ncolumns:\n  - name: A\n    description: codice\n"
    spec = load_spec(write(tmp_path, base))
    fp = spec.extraction_fingerprint("primer", "m")
    assert load_spec(write(tmp_path, base + "    required: true\noutput:\n  delimiter: ';'\n")) \
        .extraction_fingerprint("primer", "m") == fp
    assert load_spec(write(tmp_path, base + "instructions: altro\n")).extraction_fingerprint("primer", "m") != fp
    assert spec.extraction_fingerprint("primer modificato", "m") != fp
    assert spec.extraction_fingerprint("primer", "altro modello") != fp


def test_example_csv_in_prompt(tmp_path):
    (tmp_path / "es.csv").write_text("A,B\n1,2\n3,4\n5,6\n", encoding="utf-8")
    spec = load_spec(write(tmp_path, "row: x\ncolumns:\n  - name: A\nexamples:\n  csv: es.csv\n  max_rows: 2\n"))
    assert spec.example_text() == "A,B\n1,2\n3,4"
    assert "A,B\n1,2\n3,4\n```" in PromptBuilder(spec, "", 1).page_prompt(1)


def test_context_columns_are_not_repeated_in_rows():
    spec = load_spec(EXAMPLES / "finimpianti-fin-oem.yaml")
    prompt = PromptBuilder(spec, "", 10).page_prompt(1)
    assert prompt.count("DATO DI PAGINA") == 7      # 6 colonne + la regola, spiegata una volta
    assert "Nelle righe lasciale" in prompt
    assert "page_context" in build_page_schema(spec)["properties"]
