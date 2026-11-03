# 업로드 뒤 파이프라인 실행(service._run_pipeline)이 job 상태를 바꾸는 방식 테스트.
# 그래프 구조는 진짜를 쓰고, 노드 내용만 가짜로 바꿔서 LLM/DB를 호출하지 않는다.
import asyncio
from uuid import uuid4

import pytest

from app.domains.documents import jobs, service
from app.domains.documents.pipeline import graph as G
from app.domains.documents.pipeline import observe

DOCUMENTS = [
    {
        "id": "doc-0",
        "storage_bucket": "documents",
        "storage_path": "job/a.pdf",
        "original_filename": "a.pdf",
    }
]


@pytest.fixture(autouse=True)
def clear_jobs():
    jobs._jobs.clear()


def _new_job() -> object:
    job_id = uuid4()
    jobs.create_job(
        job_id,
        {
            "status": "queued",
            "document_count": 1,
            "documents": DOCUMENTS,
            "active_step": 0,
            "progress": service.STEP_PROGRESS[0],
        },
    )
    return job_id


def _patch_nodes(monkeypatch, job_id, overrides: dict | None = None) -> list[tuple]:
    # 모든 노드를 가짜로 바꾼다. 각 노드가 시작할 때 보이는 job 상태를 기록해서 돌려준다
    seen: list[tuple] = []

    # 기본으로 청크 인덱싱은 성공한 것으로 둔다(검색 가능한 문서 1개)
    defaults = {"save_chunks": {"indexed_document_count": 1}}

    def make(name):
        def node(state):
            job = jobs._jobs[job_id]
            seen.append((name, job["status"], job["active_step"], job["progress"]))
            return {**defaults, **(overrides or {})}.get(name, {})

        return node

    monkeypatch.setattr(G, "_NODES", {name: make(name) for name in G.NODE_ORDER})
    return seen


def _run(job_id):
    asyncio.run(service._run_pipeline(job_id, DOCUMENTS))
    return jobs._jobs[job_id]


def test_step_progress_matches_node_order():
    assert len(service.STEP_PROGRESS) == len(G.NODE_ORDER) == 9
    assert service.STEP_PROGRESS == sorted(set(service.STEP_PROGRESS))
    assert service.STEP_PROGRESS[-1] == 100


def test_each_node_runs_while_its_step_is_active(monkeypatch):
    job_id = _new_job()
    seen = _patch_nodes(monkeypatch, job_id, {"save": {"saved_card_count": 2}})

    job = _run(job_id)

    assert [name for name, *_ in seen] == G.NODE_ORDER
    # k번째 노드가 실행되는 동안 job은 running이고 activeStep은 k, progress는 STEP_PROGRESS[k]
    for step, (_, status, active_step, progress) in enumerate(seen):
        assert (status, active_step, progress) == ("running", step, service.STEP_PROGRESS[step])
    assert job["status"] == "completed"
    assert job["created_card_count"] == 2
    assert job["warnings"] == []


def test_completed_with_zero_cards_is_not_a_failure(monkeypatch):
    job_id = _new_job()
    _patch_nodes(monkeypatch, job_id)

    job = _run(job_id)

    assert job["status"] == "completed"
    assert job["created_card_count"] == 0


def test_warnings_are_kept_on_job(monkeypatch):
    job_id = _new_job()
    overrides = {
        "save": {"saved_card_count": 1},
        "save_chunks": {"warnings": ["청크 저장 실패: x"]},
    }
    _patch_nodes(monkeypatch, job_id, overrides)

    job = _run(job_id)

    assert job["status"] == "completed"
    assert job["warnings"] == ["청크 저장 실패: x"]


def test_nothing_usable_fails_the_job(monkeypatch):
    # 카드 0개 + 검색 가능한 문서 0개(청크 저장 실패)면 사용자가 쓸 수 있는 결과가 없다
    job_id = _new_job()
    _patch_nodes(monkeypatch, job_id, {"save_chunks": {"warnings": ["청크 저장 실패: x"]}})

    job = _run(job_id)

    assert job["status"] == "failed" and "청크 저장 실패: x" in job["message"]


def test_card_path_error_skips_to_chunk_indexing_and_completes(monkeypatch):
    job_id = _new_job()
    seen = _patch_nodes(monkeypatch, job_id, {"extract": {"error": "경험 추출 실패: boom"}})

    job = _run(job_id)

    assert [name for name, *_ in seen] == [
        "parse",
        "chunk",
        "extract",
        "embed_chunks",
        "save_chunks",
    ]
    assert job["status"] == "completed" and job["created_card_count"] == 0
    assert job["warnings"] == ["카드 생성 실패: 경험 추출 실패: boom"]


def test_parse_failure_of_some_documents_skips_the_card_path(monkeypatch):
    job_id = _new_job()
    overrides = {"parse": {"parse_failures": ["'b.pdf' 파싱 실패: x"]}}
    seen = _patch_nodes(monkeypatch, job_id, overrides)

    job = _run(job_id)

    assert [name for name, *_ in seen] == ["parse", "chunk", "embed_chunks", "save_chunks"]
    assert job["status"] == "completed"
    assert "읽지 못한 문서가 있어 카드 생성을 건너뜀" in job["warnings"]


def test_non_card_node_error_fails_job_and_skips_remaining_nodes(monkeypatch):
    job_id = _new_job()
    seen = _patch_nodes(monkeypatch, job_id, {"chunk": {"error": "청크 실패: boom"}})

    job = _run(job_id)

    assert [name for name, *_ in seen] == ["parse", "chunk"]
    assert job["status"] == "failed"
    assert job["message"] == "청크 실패: boom"


def test_node_exception_fails_job_with_short_message(monkeypatch):
    job_id = _new_job()

    def boom(state):
        raise ValueError("문서 내용 " * 200)

    _patch_nodes(monkeypatch, job_id)
    G._NODES["chunk"] = boom

    job = _run(job_id)

    assert job["status"] == "failed"
    assert job["message"].startswith("chunk 단계에서 예기치 못한 오류")
    assert len(job["message"]) < 300


def test_exception_outside_graph_fails_job_and_cleans_usage(monkeypatch):
    job_id = _new_job()

    class BrokenGraph:
        def astream(self, state, stream_mode):
            raise RuntimeError("graph build failed " + "x" * 500)

    monkeypatch.setattr(service, "build_graph", lambda: BrokenGraph())
    observe.start_job_usage(str(job_id))

    job = _run(job_id)

    assert job["status"] == "failed"
    assert job["message"].startswith("문서 분석 중 오류가 발생했습니다: graph build failed")
    assert len(job["message"]) < 350
    assert observe.finish_job_usage(str(job_id)) is None  # 집계가 남아 있지 않다


def test_job_events_end_with_completed_after_run(monkeypatch):
    job_id = _new_job()
    _patch_nodes(monkeypatch, job_id, {"save": {"saved_card_count": 3}})
    _run(job_id)

    async def collect():
        return [event async for event in service.job_events(job_id)]

    (event,) = asyncio.run(collect())
    assert event.event == "completed"
    assert '"createdCardCount":3' in event.raw_data


def test_start_pipeline_keeps_task_reference_until_done(monkeypatch):
    job_id = _new_job()
    release = asyncio.Event
    states = {}

    async def fake_run(job_id_, documents):
        await states["gate"].wait()

    monkeypatch.setattr(service, "_run_pipeline", fake_run)

    async def scenario():
        states["gate"] = release()
        service._start_pipeline(job_id, DOCUMENTS)
        held_while_running = len(service._background_tasks)
        states["gate"].set()
        await asyncio.sleep(0.05)
        return held_while_running, len(service._background_tasks)

    assert asyncio.run(scenario()) == (1, 0)
