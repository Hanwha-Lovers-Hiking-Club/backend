# 문서 업로드·작업 실행·진행 SSE 테스트.
# 저장소(repository)는 메모리 가짜로, 원문 검색 인덱싱(index_documents)은 기록용 가짜로 바꾼다.
# 외부 계약(경로, 업로드 응답, SSE 이벤트 이름·payload)은 기존 프론트(dev) 계약을 검사한다.
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domains.documents import service
from app.domains.documents.pipeline.processing import DocumentProgress

UPLOAD_URL = "/api/documents/upload"


def _job_url(job_id) -> str:
    return f"/api/documents/jobs/{job_id}"


def _events_url(job_id) -> str:
    return f"/api/documents/jobs/{job_id}/events"


def _sse_events(text: str) -> list[tuple[str, dict]]:
    # (이벤트 이름, data) 목록. 주석(keepalive)과 id 줄은 건너뛴다
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


class FakeRepository:
    def __init__(self):
        self.documents = {}
        self.jobs = {}
        self.events = []
        self.files = {}
        self.content_types = {}
        self.index_calls = []

    async def upload_file(self, path, local_path, content_type):
        self.files[path] = local_path.read_bytes()
        self.content_types[path] = content_type

    async def remove_file(self, path):
        self.files.pop(path, None)

    async def download_file(self, path):
        return self.files[path]

    async def create_document(self, document_id, filename, size, path):
        self.documents[str(document_id)] = {
            "id": str(document_id),
            "title": filename,
            "original_filename": filename,
            "storage_path": path,
        }

    async def delete_document(self, document_id):
        self.documents.pop(str(document_id), None)

    async def create_job(self, job_id, document_ids):
        now = datetime.now(timezone.utc).isoformat()
        self.jobs[str(job_id)] = {
            "id": str(job_id),
            "status": "queued",
            "document_ids": [str(item) for item in document_ids],
            "result": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._event(job_id, "queued", {"jobId": str(job_id)})

    async def check_job_store(self):
        return None

    async def _event(self, job_id, event_type, payload):
        self.events.append(
            {
                "id": len(self.events) + 1,
                "job_id": str(job_id),
                "event_type": event_type,
                "payload": payload,
                "created_at": "2026-10-31T00:00:00Z",
            }
        )

    async def record_event(self, job_id, event_type, payload, status=None):
        if status:
            self.jobs[str(job_id)]["status"] = status
        if status in service.TERMINAL:
            self.jobs[str(job_id)]["result"] = payload
        await self._event(job_id, event_type, payload)

    async def expire_stale_job(self, job_id):
        job = self.jobs[str(job_id)]
        updated_at = datetime.fromisoformat(job["updated_at"])
        if (
            job["status"] in service.TERMINAL
            or updated_at > datetime.now(timezone.utc) - service.STALE_AFTER
        ):
            return False
        job["status"] = "failed"
        job["result"] = {"errorCode": "JOB_INTERRUPTED"}
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._event(job_id, "failed", job["result"])
        return True

    async def get_job(self, job_id):
        return self.jobs.get(str(job_id))

    async def get_events(self, job_id, after_id=0):
        return [
            row for row in self.events if row["job_id"] == str(job_id) and row["id"] > after_id
        ][:100]

    async def get_documents(self, document_ids):
        return [self.documents[str(item)] for item in document_ids]


@pytest.fixture
def fake_repository(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_CHECKPOINT_PATH", tmp_path / "checkpoints.sqlite3")
    fake = FakeRepository()
    for name in (
        "upload_file",
        "remove_file",
        "download_file",
        "create_document",
        "delete_document",
        "create_job",
        "check_job_store",
        "record_event",
        "expire_stale_job",
        "get_job",
        "get_events",
        "get_documents",
    ):
        monkeypatch.setattr(service.repository, name, getattr(fake, name))

    # 원문 검색 인덱싱 가짜: 받은 문서를 기록하고, 단계 진행을 알린 뒤 전부 인덱싱했다고 답한다
    async def fake_index(job_id, documents, on_step=None):
        fake.index_calls.append(documents)
        for name in ("index_parse", "index_chunk", "embed_chunks", "save_chunks"):
            if on_step is not None:
                await on_step(name)
        return {"indexed_document_count": len(documents), "warnings": []}

    monkeypatch.setattr(service, "index_documents", fake_index)
    return fake


@pytest.fixture
def indexed(fake_repository):
    return fake_repository.index_calls


def _graph(statuses: list[str], saved: int = 0, review: int = 0):
    # 문서마다 statuses 순서대로 상태를 매기는 가짜 카드 그래프
    class Graph:
        async def astream(self, inputs, config=None, stream_mode=None, durability=None):
            assert stream_mode == "updates"
            assert durability == "sync" and config["configurable"]["thread_id"]
            progress = {}
            for source, status in zip(inputs["sources"], statuses, strict=True):
                progress[str(source.document_id)] = DocumentProgress(
                    document_id=source.document_id,
                    status=status,
                    review_cards=review,
                    saved_card_ids=[uuid4() for _ in range(saved)],
                )
            yield {"parse": {}}
            yield {"normalize": {}}
            yield {"chunk": {}}
            yield {"complete_document": {"document_progress": progress}}

    return Graph()


async def _create_job(fake, contents: list[bytes]):
    document_ids, job_id = [uuid4() for _ in contents], uuid4()
    for document_id, content in zip(document_ids, contents, strict=True):
        path = f"uploads/{document_id}.txt"
        fake.files[path] = content
        await fake.create_document(document_id, "sample.txt", len(content), path)
    await fake.create_job(job_id, document_ids)
    return job_id, document_ids


def test_upload_returns_frontend_contract_and_runs_job(client, fake_repository, monkeypatch):
    runs = []

    def graph_factory(**kwargs):
        graph = _graph(["completed"])
        original = graph.astream

        async def astream(inputs, *args, **options):
            runs.append(inputs)
            async for update in original(inputs, *args, **options):
                yield update

        graph.astream = astream
        return graph

    monkeypatch.setattr(service, "get_graph", graph_factory)
    response = client.post(
        UPLOAD_URL,
        files=[("files", ("검색 설계서.v2.MD", b"# sample", "application/octet-stream"))],
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"jobId", "documentCount", "status"}
    assert body["documentCount"] == 1 and body["status"] == "queued"
    # 문서 제목은 업로드 파일명 그대로, Content-Type은 확장자로 정한다
    [document] = fake_repository.documents.values()
    assert document["title"] == document["original_filename"] == "검색 설계서.v2.MD"
    assert list(fake_repository.content_types.values()) == ["text/markdown"]
    assert len(runs) == 1

    job = client.get(_job_url(body["jobId"]))
    assert job.status_code == 200
    assert job.json()["status"] == "completed"
    assert job.json()["result"]["indexedDocuments"] == 1


def test_finished_job_stream_sends_only_completed(client, fake_repository, monkeypatch):
    # dev 계약: 이미 끝난 작업에 접속하면 진행 이벤트 없이 completed만 받는다
    monkeypatch.setattr(service, "get_graph", lambda **kwargs: _graph(["completed"], saved=2))
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"content"]))
    asyncio.run(service.run_job(job_id, document_ids))

    stream = client.get(_events_url(job_id))
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert _sse_events(stream.text) == [
        ("completed", {"jobId": str(job_id), "status": "completed", "createdCardCount": 2})
    ]
    # 이미 받은 이벤트 이후로 재접속해도 종료 이벤트는 다시 받는다(재접속 반복 방지)
    last = client.get(_events_url(job_id), headers={"Last-Event-ID": "3"})
    assert [name for name, _ in _sse_events(last.text)] == ["completed"]


def test_zero_cards_is_a_normal_completion(client, fake_repository, monkeypatch):
    monkeypatch.setattr(service, "get_graph", lambda **kwargs: _graph(["completed"]))
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"content"]))
    asyncio.run(service.run_job(job_id, document_ids))
    [(name, data)] = _sse_events(client.get(_events_url(job_id)).text)
    assert name == "completed" and data["createdCardCount"] == 0


@pytest.mark.parametrize("statuses", [["needs_review"], ["completed", "incomplete"]])
def test_partial_result_is_not_reported_as_completed(
    client, fake_repository, monkeypatch, statuses
):
    # 프론트 계약에 partial이 없으므로 completed(성공)로 뭉개지 않고 failed로 보내며,
    # 저장·보류 카드 수를 문구에 남긴다. 상세는 작업 조회 API에 그대로 있다
    monkeypatch.setattr(service, "get_graph", lambda **kwargs: _graph(statuses, saved=1, review=1))
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"c"] * len(statuses)))
    asyncio.run(service.run_job(job_id, document_ids))

    assert fake_repository.jobs[str(job_id)]["status"] == "partial"
    [(name, data)] = _sse_events(client.get(_events_url(job_id)).text)
    assert name == "failed" and data["jobId"] == str(job_id)
    assert "일부 문서의 카드 생성이 완료되지 않았습니다" in data["message"]
    assert f"저장된 카드 {len(statuses)}개" in data["message"]
    detail = client.get(_job_url(job_id)).json()
    assert detail["status"] == "partial"
    assert [item["status"] for item in detail["result"]["documents"]] == statuses


def test_progress_maps_internal_stages_to_frontend_steps(fake_repository):
    # 내부 stage 이벤트 -> 프론트 activeStep(0~8). 재처리로 앞 단계가 다시 와도 뒤로 가지 않는다
    class Request:
        async def is_disconnected(self):
            return False

    async def run():
        job_id, _ = await _create_job(fake_repository, [b"c"])
        response = await service.job_events(job_id, Request(), None)
        stream = response.body_iterator
        first = await anext(stream)
        assert first == ('id: 1\nevent: progress\ndata: {"activeStep":0,"progress":11}\n\n')
        steps = []
        for stage in ("parse", "normalize", "chunk", "select_candidates", "extract", "finish"):
            await fake_repository.record_event(job_id, "stage", {"stage": stage})
        await fake_repository.record_event(job_id, "completed", {"savedCards": 0}, "completed")
        async for chunk in stream:
            steps.append(chunk)
        return [_sse_events(item)[0] for item in steps]

    events = asyncio.run(run())
    assert events[:-1] == [
        ("progress", {"activeStep": 1, "progress": 22}),
        ("progress", {"activeStep": 2, "progress": 33}),
        ("progress", {"activeStep": 3, "progress": 44}),
        ("progress", {"activeStep": 5, "progress": 67}),
    ]
    assert events[-1][0] == "completed"


def test_reconnect_does_not_resend_the_same_step(client, fake_repository, monkeypatch):
    async def setup():
        job_id, _ = await _create_job(fake_repository, [b"c"])
        await fake_repository.record_event(job_id, "started", {}, "running")
        await fake_repository.record_event(job_id, "stage", {"stage": "normalize"})
        return job_id

    job_id = asyncio.run(setup())
    fake_repository.jobs[str(job_id)]["status"] = "failed"  # 스트림이 끝나도록 종료 상태로 둔다
    fresh = client.get(_events_url(job_id))
    assert _sse_events(fresh.text) == [("progress", {"activeStep": 1, "progress": 22})]
    resumed = client.get(_events_url(job_id), headers={"Last-Event-ID": "3"})
    assert _sse_events(resumed.text) == []


def test_card_pipeline_failure_still_indexes_original_text(fake_repository, monkeypatch, indexed):
    class Graph:
        async def astream(self, inputs, config=None, stream_mode=None, durability=None):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover

    monkeypatch.setattr(service, "get_graph", lambda **kwargs: Graph())
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"content"]))
    asyncio.run(service.run_job(job_id, document_ids))

    assert [[doc["id"] for doc in call] for call in indexed] == [document_ids]
    job = fake_repository.jobs[str(job_id)]
    assert job["status"] == "failed"
    assert job["result"]["errorCode"] == "JOB_EXECUTION_FAILED"
    assert job["result"]["indexedDocuments"] == 1
    stages = [row["payload"].get("stage") for row in fake_repository.events]
    assert stages[-5:] == ["index_parse", "index_chunk", "embed_chunks", "save_chunks", None]


def test_indexing_failure_only_warns(fake_repository, monkeypatch):
    async def broken_index(job_id, documents, on_step=None):
        raise RuntimeError("kure down")

    monkeypatch.setattr(service, "index_documents", broken_index)
    monkeypatch.setattr(service, "get_graph", lambda **kwargs: _graph(["completed"]))
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"content"]))
    asyncio.run(service.run_job(job_id, document_ids))

    job = fake_repository.jobs[str(job_id)]
    assert job["status"] == "completed"
    assert job["result"]["indexedDocuments"] == 0
    assert "원문 검색 인덱싱 실패" in job["result"]["warnings"][0]


def test_upload_rejects_unsupported_or_empty_files(client, fake_repository):
    unsupported = client.post(
        UPLOAD_URL,
        files=[
            ("files", ("a.txt", b"x", "text/plain")),
            ("files", ("slides.ppt", b"x", "application/octet-stream")),
        ],
    )
    assert unsupported.status_code == 400
    assert unsupported.json() == {"detail": "지원하지 않는 파일 형식입니다: slides.ppt"}
    assert (
        client.post(UPLOAD_URL, files=[("files", ("empty.txt", b"", "text/plain"))]).status_code
        == 422
    )
    assert client.post(UPLOAD_URL).status_code == 422
    assert not fake_repository.files
    assert not fake_repository.documents


def test_upload_rejects_per_file_and_total_size(client, fake_repository, monkeypatch):
    monkeypatch.setattr(service, "MAX_BYTES", 8)
    monkeypatch.setattr(service, "MAX_TOTAL_BYTES", 12)
    too_large = client.post(UPLOAD_URL, files=[("files", ("large.txt", b"a" * 9, "text/plain"))])
    assert too_large.status_code == 413
    total = client.post(
        UPLOAD_URL,
        files=[
            ("files", ("first.txt", b"a" * 7, "text/plain")),
            ("files", ("second.txt", b"b" * 6, "text/plain")),
        ],
    )
    assert total.status_code == 413
    assert not fake_repository.files
    assert not fake_repository.documents


def test_unknown_or_invalid_job(client, fake_repository):
    job_id = uuid4()
    assert client.get(_job_url(job_id)).status_code == 404
    response = client.get(_events_url(job_id))
    assert response.status_code == 404
    assert response.json() == {"detail": "작업을 찾을 수 없습니다."}
    assert client.get(_events_url("not-a-uuid")).status_code == 422


def test_upload_registration_failure_cleans_storage_and_documents(
    client, fake_repository, monkeypatch
):
    async def unavailable(job_id, document_ids):
        raise RuntimeError("job RPC unavailable")

    monkeypatch.setattr(service.repository, "create_job", unavailable)
    response = client.post(
        UPLOAD_URL, files=[("files", ("sample.txt", b"sample text", "text/plain"))]
    )
    assert response.status_code == 503
    assert not fake_repository.files
    assert not fake_repository.documents


def test_missing_job_store_fails_before_storage_write(client, fake_repository, monkeypatch):
    async def unavailable():
        raise RuntimeError("missing table")

    monkeypatch.setattr(service.repository, "check_job_store", unavailable)
    response = client.post(
        UPLOAD_URL, files=[("files", ("sample.txt", b"sample text", "text/plain"))]
    )
    assert response.status_code == 503
    assert not fake_repository.files
    assert not fake_repository.documents


@pytest.mark.parametrize("status", ["queued", "running"])
def test_stale_job_becomes_persisted_failure(client, fake_repository, status):
    job_id, document_id = uuid4(), uuid4()
    asyncio.run(fake_repository.create_job(job_id, [document_id]))
    fake_repository.jobs[str(job_id)]["status"] = status
    fake_repository.jobs[str(job_id)]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=31)
    ).isoformat()

    response = client.get(_job_url(job_id))
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["result"] == {"errorCode": "JOB_INTERRUPTED"}
    [(name, data)] = _sse_events(client.get(_events_url(job_id)).text)
    assert name == "failed" and "중단" in data["message"]
    assert sum(row["event_type"] == "failed" for row in fake_repository.events) == 1


def test_recent_running_job_is_not_expired(client, fake_repository):
    job_id = uuid4()
    asyncio.run(fake_repository.create_job(job_id, [uuid4()]))
    fake_repository.jobs[str(job_id)]["status"] = "running"
    response = client.get(_job_url(job_id))
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_sse_keepalive_is_a_comment(fake_repository, monkeypatch):
    class Request:
        async def is_disconnected(self):
            return False

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(service, "KEEPALIVE_SECONDS", 0)
    monkeypatch.setattr(service.asyncio, "sleep", no_sleep)
    job_id = uuid4()

    async def run():
        await fake_repository.create_job(job_id, [uuid4()])
        fake_repository.jobs[str(job_id)]["status"] = "running"
        response = await service.job_events(job_id, Request(), None)
        stream = response.body_iterator
        assert "event: progress" in await anext(stream)
        assert await anext(stream) == ": keepalive\n\n"
        await fake_repository.record_event(
            job_id, "failed", {"errorCode": "JOB_INTERRUPTED"}, "failed"
        )
        assert "event: failed" in await anext(stream)

    asyncio.run(run())


def test_sse_delivers_expiration_after_initial_event_read(client, fake_repository, monkeypatch):
    job_id = uuid4()
    asyncio.run(fake_repository.create_job(job_id, [uuid4()]))
    fake_repository.jobs[str(job_id)]["status"] = "running"
    original_get_events = fake_repository.get_events
    reads = 0

    async def get_events_with_interruption(job_id, after_id=0):
        nonlocal reads
        rows = await original_get_events(job_id, after_id)
        reads += 1
        if reads == 1:
            fake_repository.jobs[str(job_id)]["updated_at"] = (
                datetime.now(timezone.utc) - timedelta(minutes=31)
            ).isoformat()
        return rows

    monkeypatch.setattr(service.repository, "get_events", get_events_with_interruption)
    stream = client.get(_events_url(job_id))
    assert stream.status_code == 200
    assert [name for name, _ in _sse_events(stream.text)] == ["progress", "failed"]


def test_job_failure_is_persisted(fake_repository, monkeypatch):
    class Graph:
        async def astream(self, inputs, config=None, stream_mode=None, durability=None):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover

    monkeypatch.setattr(service, "get_graph", lambda **kwargs: Graph())
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"content"]))
    asyncio.run(service.run_job(job_id, document_ids))
    assert fake_repository.jobs[str(job_id)]["status"] == "failed"
    assert fake_repository.events[-1]["event_type"] == "failed"
    assert fake_repository.events[-1]["payload"]["errorCode"] == "JOB_EXECUTION_FAILED"


def test_partial_result_survives_later_graph_failure(fake_repository, monkeypatch):
    class Graph:
        async def astream(self, inputs, config=None, stream_mode=None, durability=None):
            first = inputs["sources"][0].document_id
            yield {
                "complete_document": {
                    "document_progress": {
                        str(first): DocumentProgress(document_id=first, status="completed")
                    }
                }
            }
            raise RuntimeError("later document failed")

    monkeypatch.setattr(service, "get_graph", lambda **kwargs: Graph())
    job_id, document_ids = asyncio.run(_create_job(fake_repository, [b"content", b"content"]))
    asyncio.run(service.run_job(job_id, document_ids))
    result = fake_repository.jobs[str(job_id)]["result"]
    assert fake_repository.jobs[str(job_id)]["status"] == "partial"
    assert [item["status"] for item in result["documents"]] == ["completed", "failed"]
