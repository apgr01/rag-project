from ragpdf.indexing.compare import compare_rows, filter_reference, normalize_value


def test_normalize_value():
    assert normalize_value("  fin 001  af ") == "FIN 001 AF"
    assert normalize_value("4.0") == "4"
    assert normalize_value(None) == ""


def test_compare_rows_counts_duplicates_and_differences():
    produced = [{"A": "x", "B": "1"}, {"A": "x", "B": "1"}, {"A": "y", "B": "2"}, {"A": "z", "B": "9"}]
    reference = [{"A": "X", "B": "1.0"}, {"A": "y", "B": "2"}, {"A": "w", "B": "3"}]
    c = compare_rows(produced, reference, ["A", "B"])
    assert (c.produced_rows, c.reference_rows, c.matched) == (4, 3, 2)
    assert c.missing == [{"A": "w", "B": "3"}]
    assert c.extra == [{"A": "x", "B": "1"}, {"A": "z", "B": "9"}]
    assert round(c.recall, 3) == 0.667 and c.precision == 0.5


def test_filter_reference_by_source_and_pages():
    rows = [
        {"File_origine": "Catalogo 1 LOMBARDINI rel 1501.pdf", "Pagina": "40", "X": "a"},
        {"File_origine": "Catalogo 1 LOMBARDINI rel 1501.pdf", "Pagina": "41", "X": "b"},
        {"File_origine": "Catalogo 2 VM rel 1608.pdf", "Pagina": "40", "X": "c"},
    ]
    out = filter_reference(rows, "File_origine", "Catalogo 1 LOMBARDINI rel 1501.pdf", "Pagina", [40])
    assert [r["X"] for r in out] == ["a"]
    assert len(filter_reference(rows, "File_origine", "catalogo 2 vm rel 1608.PDF", None, None)) == 1
