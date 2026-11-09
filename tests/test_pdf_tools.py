import pymupdf
import pytest

from ragpdf.core.pdf_tools import (
    build_subset_pdf,
    clean_text,
    has_text_layer,
    page_mapping_text,
    page_text,
)


def test_subset_keeps_selected_pages_in_order(tmp_path, pdf_factory):
    pdf = pdf_factory(pages=6, prefix="Pagina")
    out = build_subset_pdf(pdf, [5, 1, 3], tmp_path / "estratto.pdf")
    with pymupdf.open(out) as doc:
        texts = [doc[i].get_text().strip() for i in range(len(doc))]
    assert texts == ["Pagina 5", "Pagina 1", "Pagina 3"]


def test_subset_errors(tmp_path, pdf_factory):
    pdf = pdf_factory(pages=2)
    with pytest.raises(ValueError, match="non esiste"):
        build_subset_pdf(pdf, [3], tmp_path / "x.pdf")
    with pytest.raises(ValueError, match="Nessuna"):
        build_subset_pdf(pdf, [], tmp_path / "x.pdf")


def test_mapping_text():
    text = page_mapping_text([1, 4, 102], 120)
    assert "estratto di 3 pagine" in text and "120 pagine" in text
    assert "pagina 3 dell'estratto = pagina 102 del documento originale" in text


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("LLLOOOMMMBBBAAARRRDDDIIINNNIII  motori", "LOMBARDINI motori"),
        ("codice 1100 e 111222 restano", "codice 1100 e 111222 restano"),
        ("FIN040RS00 III FILL", "FIN040RS00 III FILL"),
        ("testo(cid:12)sporco\n\n   \n riga  due ", "testo sporco\nriga due"),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_page_text_and_text_layer(tmp_path, pdf_factory):
    pdf = pdf_factory(pages=2, prefix="FIN102AF codice")
    assert page_text(pdf, 2) == "FIN102AF codice 2"
    assert has_text_layer(pdf)
    blank = tmp_path / "vuoto.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(blank)
    assert page_text(blank, 1) == "" and not has_text_layer(blank)
    assert page_text(pdf, 1, max_chars=5).endswith("[... testo troncato ...]")
