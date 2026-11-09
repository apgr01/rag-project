import pytest

from ragpdf.core.documents import DocumentError, DuplicateDocumentError
from ragpdf.core.workspace import slugify


def test_slugify():
    assert slugify("Catalogo 5 KUBOTA rel 1010") == "catalogo-5-kubota-rel-1010"
    assert slugify("Più è già") == "piu-e-gia"
    assert slugify("***") == "documento"


def test_add_and_get(store, pdf_factory):
    doc = store.add(pdf_factory("Catalogo 5 KUBOTA rel 1010.pdf", pages=2))
    assert doc.id == "catalogo-5-kubota-rel-1010"
    assert doc.info.page_count == 2
    assert doc.pdf_path.exists()

    assert store.get("catalogo-5-kubota-rel-1010").id == doc.id
    assert store.get("Catalogo 5 KUBOTA rel 1010.pdf").id == doc.id
    assert store.get("kubota").id == doc.id          # parte dell'id
    assert [d.id for d in store.list()] == [doc.id]


def test_duplicate_content_is_detected(store, pdf_factory, tmp_path):
    pdf = pdf_factory("a.pdf")
    store.add(pdf)
    copy = tmp_path / "copia con altro nome.pdf"
    copy.write_bytes(pdf.read_bytes())
    with pytest.raises(DuplicateDocumentError) as exc:
        store.add(copy)
    assert exc.value.existing.id == "a"


def test_same_name_different_content_gets_new_id(store, pdf_factory, tmp_path):
    first = store.add(pdf_factory("cat.pdf", prefix="A"))
    other = tmp_path / "altra"
    other.mkdir()
    from conftest import make_pdf

    second = store.add(make_pdf(other / "cat.pdf", prefix="B"))
    assert (first.id, second.id) == ("cat", "cat-2")


def test_get_errors(store, pdf_factory):
    with pytest.raises(DocumentError, match="non contiene documenti"):
        store.get("x")
    store.add(pdf_factory("motori lombardini.pdf", prefix="L"))
    store.add(pdf_factory("motori kubota.pdf", prefix="K"))
    with pytest.raises(DocumentError, match="più documenti"):
        store.get("motori")
    with pytest.raises(DocumentError, match="non trovato"):
        store.get("deutz")


def test_not_a_pdf(store, tmp_path):
    txt = tmp_path / "file.txt"
    txt.write_text("ciao")
    with pytest.raises(DocumentError, match="non è un PDF"):
        store.add(txt)


def test_primer_page_choice(store, pdf_factory):
    from ragpdf.primer import MAX_SELECTED_PAGES, choose_pages, sample_pages

    small = store.add(pdf_factory("piccolo.pdf", pages=5))
    assert choose_pages(small, None)[0] is None                    # PDF intero
    assert choose_pages(small, [1, 3]) == ([1, 3], "estratto di 2 pagine scelte")
    big = store.add(pdf_factory("grande.pdf", pages=100, prefix="G"))
    auto, what = choose_pages(big, None)
    assert auto == sample_pages(100) and "automatico" in what and auto[:8] == list(range(1, 9))
    with pytest.raises(ValueError, match="massimo"):
        choose_pages(big, list(range(1, MAX_SELECTED_PAGES + 2)))
