"""Test del pool di chiavi: nessuna chiamata di rete, orologio simulato."""

import math

import pytest
from google.genai import errors

from ragpdf.core.config import GeminiSettings
from ragpdf.core.gemini import GeminiClient, GeminiError, QuotaExhaustedError, load_api_keys


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def quota_error(daily: bool):
    limit = "GenerateRequestsPerDayPerProjectPerModel" if daily else "GenerateRequestsPerMinutePerProject"
    return errors.ClientError(429, {"error": {"code": 429, "message": f"Quota exceeded, limit: {limit}"}})


def make_client(keys, settings=None):
    clock = FakeClock()
    client = GeminiClient(
        keys,
        settings or GeminiSettings(requests_per_minute_per_key=60),
        client_factory=lambda key: key,     # il "client SDK" è la chiave stessa
        sleep=clock.sleep,
        clock=clock.clock,
        log=lambda msg: None,
    )
    return client, clock


def test_load_api_keys(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("GEMINI_API_KEY"):
            monkeypatch.delenv(name)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY_02=b\nGEMINI_API_KEY_01=a\nGEMINI_API_KEY_03=\nGEMINI_API_KEYS=c, a\n")
    assert load_api_keys(env) == ["a", "b", "c"]


def test_rate_limit_is_per_key_and_shared():
    client, clock = make_client(["k1", "k2"], GeminiSettings(requests_per_minute_per_key=30))
    used = [client._call(lambda c: c) for _ in range(4)]
    assert used == ["k1", "k2", "k1", "k2"]
    assert clock.sleeps == [2.0]  # la terza richiesta attende che k1 torni libera (60/30 s)


def test_minute_quota_rotates_key():
    client, _ = make_client(["k1", "k2"])
    calls = []

    def action(key):
        calls.append(key)
        if key == "k1":
            raise quota_error(daily=False)
        return "ok"

    assert client._call(action) == "ok"
    assert calls == ["k1", "k2"]
    assert client._slots[0].paused_until == pytest.approx(60.0)


def test_daily_quota_excludes_key_and_finally_raises():
    client, _ = make_client(["k1", "k2"])

    def action(key):
        raise quota_error(daily=True)

    with pytest.raises(QuotaExhaustedError):
        client._call(action)
    assert all(math.isinf(s.paused_until) for s in client._slots)


def test_server_errors_are_retried_with_backoff():
    client, clock = make_client(["k1"])
    attempts = {"n": 0}

    def action(key):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise errors.ServerError(503, {"error": {"code": 503, "message": "overloaded"}})
        return "ok"

    assert client._call(action) == "ok"
    assert 2 in clock.sleeps and 4 in clock.sleeps


def test_bad_request_fails_immediately():
    client, _ = make_client(["k1", "k2"])
    calls = []

    def action(key):
        calls.append(key)
        raise errors.ClientError(400, {"error": {"code": 400, "message": "modello inesistente"}})

    with pytest.raises(GeminiError, match="modello inesistente"):
        client._call(action)
    assert calls == ["k1"]


def test_no_keys():
    with pytest.raises(GeminiError, match="Nessuna chiave"):
        GeminiClient([], GeminiSettings())


def test_pdf_upload_waits_until_active(tmp_path):
    from types import SimpleNamespace as NS

    states = iter(["PROCESSING", "PROCESSING", "ACTIVE"])

    class Files:
        def upload(self, file):
            return NS(name="files/1", state=NS(name=next(states)))

        def get(self, name):
            return NS(name=name, state=NS(name=next(states)))

        def delete(self, name):
            self.deleted = name

    class Models:
        def generate_content(self, model, contents, config):
            return NS(text=" primer ")

    sdk = NS(files=Files(), models=Models())
    clock = FakeClock()
    client = GeminiClient(["k"], GeminiSettings(), client_factory=lambda k: sdk,
                          sleep=clock.sleep, clock=clock.clock, log=lambda m: None)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    assert client.generate_text_from_pdf("p", pdf) == "primer"
    assert clock.sleeps == [3, 3] and sdk.files.deleted == "files/1"
