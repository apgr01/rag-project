from ragpdf.core.manifest import DONE, FAILED, Manifest


def test_resume_logic(tmp_path):
    path = tmp_path / "manifest.json"
    m = Manifest.load_or_create(path, total_pages=4, meta={"fingerprint": "x"})
    assert m.pages_to_process(max_attempts=2) == [1, 2, 3, 4]

    m.mark_done(1)
    m.mark_failed(2, "errore")
    reloaded = Manifest.load_or_create(path, total_pages=4)
    assert reloaded.page(1)["status"] == DONE
    assert reloaded.page(2)["status"] == FAILED
    assert reloaded.meta["fingerprint"] == "x"
    assert reloaded.pages_to_process(max_attempts=2) == [2, 3, 4]
    assert reloaded.pages_to_process(max_attempts=2, only=[1, 2]) == [2]

    reloaded.mark_failed(2, "ancora")
    assert reloaded.pages_to_process(max_attempts=2) == [3, 4]  # tentativi esauriti
    assert reloaded.failed_pages() == {2: "ancora"}

    reloaded.reset({"fingerprint": "y"})
    assert reloaded.pages_to_process(max_attempts=2) == [1, 2, 3, 4]
    assert reloaded.meta == {"fingerprint": "y"}
