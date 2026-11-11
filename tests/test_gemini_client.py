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


def quota_error(daily: bool, retry: str | None = "30s"):
    """429 come lo restituisce Google: il tipo di quota è solo nei dettagli, non nel messaggio."""
    quota_id = ("GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily
                else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": quota_id, "quotaValue": "10"}]}]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    message = ("You exceeded your current quota. * Quota exceeded for metric: "
               "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 10")
    return errors.ClientError(429, {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED",
                                              "details": details}})


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
    # ritmo complessivo massimo = 2 chiavi x 30/min = 60/min: una richiesta al secondo,
    # e ogni chiave non più di una ogni 2 secondi
    assert clock.sleeps == [1.0, 1.0, 1.0]


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
    assert client._slots[0].paused_until == pytest.approx(30.0)   # retryDelay indicato da Google


def test_daily_quota_excludes_key_and_finally_raises():
    client, _ = make_client(["k1", "k2"])

    def action(key):
        raise quota_error(daily=True)

    with pytest.raises(QuotaExhaustedError):
        client._call(action)
    assert all(math.isinf(s.paused_until) for s in client._slots)


def overloaded():
    return errors.ServerError(503, {"error": {"code": 503, "message": "This model is currently experiencing "
                                              "high demand.", "status": "UNAVAILABLE"}})


def test_overload_is_retried_at_fixed_interval_without_failing():
    """503 'high demand': attese fisse (non raddoppiate) e nessun tentativo consumato."""
    client, clock = make_client(["k1"], GeminiSettings(requests_per_minute_per_key=600, retry_delay_seconds=5))
    attempts = {"n": 0}

    def action(key):
        attempts["n"] += 1
        if attempts["n"] <= 10:                 # più dei 6 tentativi: il sovraccarico non li consuma
            raise overloaded()
        return "ok"

    assert client._call(action) == "ok"
    waits = [s for s in clock.sleeps if s >= 5]
    assert len(waits) == 10 and all(5 <= s <= 6 for s in waits)


def test_overload_that_lasts_too_long_stops_cleanly():
    from ragpdf.core.gemini import ServiceUnavailableError

    client, clock = make_client(["k1", "k2"], GeminiSettings(retry_delay_seconds=30, max_wait_minutes=2))

    def action(key):
        raise overloaded()

    with pytest.raises(ServiceUnavailableError, match="oltre 2 minuti"):
        client._call(action)
    assert clock.now < 4 * 60


def test_other_server_errors_use_fixed_delay_and_attempt_limit():
    client, clock = make_client(["k1"], GeminiSettings(retry_delay_seconds=3, max_retries=3))

    def action(key):
        raise errors.ServerError(500, {"error": {"code": 500, "message": "internal"}})

    with pytest.raises(GeminiError, match="ripetuti tentativi"):
        client._call(action)
    waits = [s for s in clock.sleeps if s >= 3]
    assert len(waits) == 3 and all(3 <= s <= 4 for s in waits)


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


def test_large_pdf_upload_waits_until_active(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    import ragpdf.core.gemini as gemini_module

    monkeypatch.setattr(gemini_module, "INLINE_PDF_MAX_BYTES", 1)  # forza la Files API

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
            assert contents[0].name == "files/1" and contents[-1] == "p"
            return NS(text=" primer ")

    sdk = NS(files=Files(), models=Models())
    clock = FakeClock()
    client = GeminiClient(["k"], GeminiSettings(), client_factory=lambda k: sdk,
                          sleep=clock.sleep, clock=clock.clock, log=lambda m: None)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    assert client.generate_text("p", pdf=pdf) == "primer"
    assert clock.sleeps == [3, 3] and sdk.files.deleted == "files/1"


def test_small_pdf_is_sent_inline(tmp_path):
    from types import SimpleNamespace as NS

    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen["contents"] = contents
            seen["model"] = model
            seen["config"] = config
            return NS(text='{"ok": true}')

    sdk = NS(files=None, models=Models())
    clock = FakeClock()
    client = GeminiClient(["k"], GeminiSettings(model="vision-m", text_model="text-m", media_resolution="high",
                                                thinking_level="low"),
                          client_factory=lambda k: sdk, log=lambda m: None, sleep=clock.sleep, clock=clock.clock)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 piccolo")
    assert client.generate_json("p", {"type": "object"}, pdf=pdf) == {"ok": True}
    assert seen["config"].thinking_config.thinking_level.value == "LOW"
    part = seen["contents"][0]
    assert part.inline_data.mime_type == "application/pdf" and seen["model"] == "vision-m"
    assert client.generate_text("ciao") is not None and seen["model"] == "text-m"


@pytest.mark.parametrize(
    "finish, block, expected",
    [("STOP", None, None), ("MAX_TOKENS", None, "lunghezza massima"), ("SAFETY", None, "sicurezza"),
     ("RECITATION", None, "recitation"), (None, "PROHIBITED_CONTENT", "bloccata")],
)
def test_incomplete_responses_are_not_retried(finish, block, expected):
    from types import SimpleNamespace as NS

    from ragpdf.core.gemini import IncompleteResponseError

    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(1)
            candidates = [NS(finish_reason=NS(name=finish))] if finish else []
            feedback = NS(block_reason=NS(name=block)) if block else None
            return NS(text='{"a": 1}', candidates=candidates, prompt_feedback=feedback)

    clock = FakeClock()
    client = GeminiClient(["k1", "k2"], GeminiSettings(), client_factory=lambda k: NS(models=Models()),
                          log=lambda m: None, sleep=clock.sleep, clock=clock.clock)
    if expected is None:
        assert client.generate_json("p", {"type": "object"}) == {"a": 1}
    else:
        with pytest.raises(IncompleteResponseError, match=expected):
            client.generate_json("p", {"type": "object"})
    assert len(calls) == 1


def test_usage_is_accumulated():
    from types import SimpleNamespace as NS

    class Models:
        def generate_content(self, model, contents, config):
            meta = NS(prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=50)
            return NS(text="ok", candidates=[], prompt_feedback=None, usage_metadata=meta)

    clock = FakeClock()
    client = GeminiClient(["k"], GeminiSettings(), client_factory=lambda k: NS(models=Models()), log=lambda m: None,
                          sleep=clock.sleep, clock=clock.clock)
    client.generate_text("a")
    client.generate_text("b")
    u = client.usage
    assert (u.requests, u.input_tokens, u.output_tokens) == (2, 2000, 500)
    assert "2 richieste" in u.describe() and "2.000" in u.describe()


def test_daily_quota_recognized_from_details_only():
    """Il messaggio non dice 'giornaliera': lo dicono solo i dettagli (quotaId)."""
    client, _ = make_client(["k1", "k2"])
    calls = []

    def action(key):
        calls.append(key)
        if key == "k1":
            raise quota_error(daily=True)
        return "ok"

    assert client._call(action) == "ok"
    assert math.isinf(client._slots[0].paused_until)
    assert client._call(lambda k: k) == "k2"            # k1 non viene più usata


def test_shared_project_quota_slows_down_instead_of_failing():
    """Il caso del log: 10 chiavi dello stesso progetto, limite reale 10 richieste/minuto.

    Le richieste non devono fallire: il ritmo complessivo scende e si aspetta il retryDelay.
    """
    logs = []
    clock = FakeClock()
    client = GeminiClient([f"k{i}" for i in range(10)], GeminiSettings(requests_per_minute_per_key=10),
                          client_factory=lambda k: k, sleep=clock.sleep, clock=clock.clock, log=logs.append)
    window: list[float] = []

    def action(key):
        # il "progetto" accetta al massimo 10 richieste in 60 secondi
        recent = [t for t in window if clock.now - t < 60]
        if len(recent) >= 10:
            raise quota_error(daily=False, retry="20s")
        window.append(clock.now)
        return "ok"

    results = [client._call(action) for _ in range(40)]
    assert results == ["ok"] * 40
    assert client._limit.rpm < 20                      # partito da 100/min, si è adattato
    slowdowns = [m for m in logs if "rallento" in m]
    assert 1 <= len(slowdowns) <= 8                    # pochi messaggi, non uno per errore
    assert not any("passo a un'altra chiave" in m for m in logs)


def test_quota_that_never_resets_stops_cleanly():
    client, clock = make_client(["k1", "k2"])

    def action(key):
        raise quota_error(daily=False, retry="60s")

    with pytest.raises(QuotaExhaustedError, match="oltre 10 minuti"):
        client._call(action)


def test_configured_total_limit():
    clock = FakeClock()
    client = GeminiClient(["a", "b", "c"], GeminiSettings(requests_per_minute_per_key=10,
                                                          max_requests_per_minute_total=6),
                          client_factory=lambda k: k, sleep=clock.sleep, clock=clock.clock, log=lambda m: None)
    for _ in range(3):
        client._call(lambda k: k)
    assert clock.sleeps == [10.0, 10.0]                # 6/min in totale = una ogni 10 s


def test_retry_delay_from_message_when_details_missing():
    from ragpdf.core.gemini import _classify

    exc = errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded. Please retry in 12.5s."}})
    failure = _classify(exc)
    assert failure.retry_delay == 12.5 and not failure.is_daily_quota
