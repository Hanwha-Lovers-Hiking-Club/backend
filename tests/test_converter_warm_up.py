# 문서 변환기(docling) 로딩: 한 번만 만들고, 서버 시작 때 백그라운드로 미리 불러 두는지 테스트.
# 실제 docling은 불러오지 않는다(20초 걸림). _build_converter를 가짜로 바꾼다.
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.domains.documents import service
from app.domains.documents.pipeline.nodes import parse as P
from app.main import app


@pytest.fixture(autouse=True)
def reset_converter(monkeypatch):
    monkeypatch.setattr(P, "_converter", None)


def test_converter_is_built_once_even_when_called_concurrently(monkeypatch):
    # 서버 시작 때의 미리 로딩과 첫 업로드가 겹쳐도 로딩은 한 번이고, 늦게 온 쪽은 끝나길 기다린다
    built = []

    def slow_build():
        built.append(1)
        time.sleep(0.2)
        return object()

    monkeypatch.setattr(P, "_build_converter", slow_build)
    results = []
    threads = [threading.Thread(target=lambda: results.append(P.get_converter())) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(built) == 1
    assert len({id(r) for r in results}) == 1


def test_failed_build_is_not_cached(monkeypatch):
    attempts = []

    def flaky_build():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("model download failed")
        return "converter"

    monkeypatch.setattr(P, "_build_converter", flaky_build)

    with pytest.raises(RuntimeError):
        P.get_converter()
    assert P.get_converter() == "converter"


def test_warm_up_loads_converter_in_background_thread(monkeypatch, caplog):
    started = threading.Event()
    release = threading.Event()
    caller_thread = threading.current_thread()
    seen = {}

    def fake_get_converter():
        seen["thread"] = threading.current_thread()
        started.set()
        release.wait(2)

    monkeypatch.setattr(service, "get_converter", fake_get_converter)
    caplog.set_level(logging.INFO, logger="app")

    thread = service.start_converter_warm_up()

    # 로딩이 끝나지 않았어도 바로 돌아온다(서버 시작을 붙잡지 않는다)
    assert started.wait(2)
    assert thread.is_alive() and thread.daemon
    assert seen["thread"] is not caller_thread
    release.set()
    thread.join(2)
    assert "문서 변환기 준비 완료" in caplog.text


def test_warm_up_failure_is_logged_not_raised(monkeypatch, caplog):
    def broken():
        raise RuntimeError("no network")

    monkeypatch.setattr(service, "get_converter", broken)
    caplog.set_level(logging.INFO, logger="app")

    service.start_converter_warm_up().join(2)

    assert "문서 변환기 미리 로딩 실패" in caplog.text
    assert "준비 완료" not in caplog.text


def test_server_startup_starts_warm_up(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "start_converter_warm_up", lambda: calls.append(1))

    with TestClient(app):  # with로 열어야 서버 시작/종료(lifespan)가 실행된다
        assert calls == [1]
