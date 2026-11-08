import pytest

from ragpdf.core.render import parse_page_ranges, render_pages


def test_parse_page_ranges():
    assert parse_page_ranges(None, 10) is None
    assert parse_page_ranges("  ", 10) is None
    assert parse_page_ranges("1-3, 5", 10) == [1, 2, 3, 5]
    assert parse_page_ranges("8-", 10) == [8, 9, 10]
    assert parse_page_ranges("-2", 10) == [1, 2]


@pytest.mark.parametrize("bad", ["0", "5-3", "11", "a-b"])
def test_parse_page_ranges_errors(bad):
    with pytest.raises(ValueError):
        parse_page_ranges(bad, 10)


def test_render_pages_cache_and_dpi_change(tmp_path, pdf_factory):
    pdf = pdf_factory(pages=3)
    out = tmp_path / "pages"
    images = render_pages(pdf, out, dpi=50, show_progress=False)
    assert sorted(images) == [1, 2, 3] and all(p.exists() for p in images.values())

    mtime = images[1].stat().st_mtime_ns
    render_pages(pdf, out, dpi=50, pages=[1], show_progress=False)
    assert images[1].stat().st_mtime_ns == mtime  # già presente: non rigenerata

    render_pages(pdf, out, dpi=60, pages=[1], show_progress=False)
    assert images[1].stat().st_mtime_ns != mtime  # DPI cambiato: rigenerata
