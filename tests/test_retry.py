import asyncio

import pytest

from app.domains.documents.pipeline import retry as R
from app.domains.documents.pipeline.nodes import parse as P


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(R.time, "sleep", slept.append)

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)
    return slept


def _flaky(fail_times: int, error: type[Exception] = RuntimeError):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise error("boom")
        return "ok"

    return fn, calls


def test_succeeds_after_failures_with_exponential_backoff(no_sleep):
    fn, calls = _flaky(2)
    assert R.retry_sync(fn, retries=2, backoff=1.0) == "ok" and calls["n"] == 3
    assert 1.0 <= no_sleep[0] < 1.5 and 2.0 <= no_sleep[1] < 2.5


def test_raises_after_retries_are_used_up():
    fn, calls = _flaky(99)
    with pytest.raises(RuntimeError):
        R.retry_sync(fn, retries=2)
    assert calls["n"] == 3


def test_only_listed_errors_are_retried(no_sleep):
    fn, calls = _flaky(1, error=KeyError)
    with pytest.raises(KeyError):
        R.retry_sync(fn, retries=2, retry_on=(ValueError,))
    assert calls["n"] == 1 and no_sleep == []


def test_zero_backoff_never_sleeps(no_sleep):
    fn, _ = _flaky(1, error=ValueError)
    assert R.retry_sync(fn, retries=1, backoff=0, retry_on=(ValueError,)) == "ok"
    assert no_sleep == []


def test_retries_are_logged(caplog):
    fn, _ = _flaky(1)
    with caplog.at_level("WARNING"):
        R.retry_sync(fn, retries=1, name="테스트 호출")
    assert "테스트 호출 실패, 재시도 1/1" in caplog.text


def test_async_version():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert asyncio.run(R.retry_async(fn)) == "ok" and calls["n"] == 3


def test_parse_retries_the_download(monkeypatch):
    calls = {"n": 0}

    def flaky_download(_path):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("storage down")
        return "한글 본문".encode()

    monkeypatch.setattr(P.repository, "download_file", flaky_download)
    doc = {"id": "d", "storage_path": "p", "original_filename": "a.txt"}
    result = asyncio.run(P.parse({"documents": [doc]}))
    assert calls["n"] == 3 and result["parsed_documents"][0]["text"] == "한글 본문"


def test_llm_format_errors_are_retried_once_but_transport_errors_are_not():
    from app.domains.documents.pipeline.llm import invoke_with_retry

    class Llm:
        def __init__(self, errors):
            self.errors, self.calls = list(errors), 0

        def invoke(self, _messages):
            self.calls += 1
            if self.errors:
                raise self.errors.pop(0)
            return "ok"

    flaky = Llm([ValueError("스키마 위반")])
    assert invoke_with_retry(flaky, []) == "ok" and flaky.calls == 2

    always_bad = Llm([ValueError("a"), ValueError("b")])
    with pytest.raises(ValueError):
        invoke_with_retry(always_bad, [])
    assert always_bad.calls == 2  # 최초 1회 + 재시도 1회

    transport = Llm([ConnectionError("net")])
    with pytest.raises(ConnectionError):
        invoke_with_retry(transport, [])
    assert transport.calls == 1  # 전송 오류는 SDK 몫이라 여기서 재시도하지 않는다
