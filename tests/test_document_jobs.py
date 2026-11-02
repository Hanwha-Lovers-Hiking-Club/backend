from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domains.documents import service
from app.domains.documents.pipeline.processing import DocumentProgress


class FakeRepository:
    def __init__(self):
        self.documents = {}
        self.jobs = {}
        self.events = []
        self.files = {}

    async def upload_file(self, path, local_path, content_type):
        self.files[path] = local_path.read_bytes()

    async def remove_file(self, path):
        self.files.pop(path, None)

    async def download_file(self, path):
        return self.files[path]

    async def create_document(self, document_id, filename, size, path):
        self.documents[str(document_id)] = {
            "id": str(document_id),
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
    return fake


def test_upload_job_and_late_sse_reconnect(client, fake_repository, monkeypatch):
    runs = []

    class Graph:
        async def astream(self, inputs, config=None, stream_mode=None, durability=None):
            assert stream_mode == "updates"
            assert durability == "sync" and config["configurable"]["thread_id"]
            runs.append(inputs)
            progress = {}
            for source in inputs["sources"]:
                progress[str(source.document_id)] = DocumentProgress(
                    document_id=source.document_id,
                    status="completed",
                    rejected_cards=1,
                    saved_card_ids=[],
                )
            yield {"parse": {}}
            yield {"complete_document": {"document_progress": progress}}

    monkeypatch.setattr(service, "get_graph", lambda **kwargs: Graph())
    response = client.post(
        "/api/upload",
        files=[("files", ("sample.txt", b"sample text", "text/plain"))],
    )
    assert response.status_code == 202, response.text
    uploaded = response.json()
    assert uploaded["status"] == "queued"
    assert uploaded["documents"][0]["originalFilename"] == "sample.txt"
    assert len(fake_repository.documents) == 1
    assert len(runs) == 1
    job_id = uploaded["jobId"]
    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "completed"
    assert job.json()["result"]["savedCards"] == 0
    assert job.json()["result"]["rejectedCards"] == 1

    stream = client.get(f"/api/jobs/{job_id}/events")
    assert stream.status_code == 200
    assert "event: queued" in stream.text
    assert "event: completed" in stream.text
    last = client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "3"})
    assert "event: queued" not in last.text
    assert "event: completed" in last.text
    assert len(runs) == 1


def test_upload_rejects_unsupported_or_empty_files(client, fake_repository):
    assert (
        client.post(
            "/api/upload", files=[("files", ("bad.exe", b"x", "application/octet-stream"))]
        ).status_code
        == 422
    )
    assert (
        client.post("/api/upload", files=[("files", ("empty.txt", b"", "text/plain"))]).status_code
        == 422
    )
    assert not fake_repository.files
    assert not fake_repository.documents


def test_upload_rejects_per_file_and_total_size(client, fake_repository, monkeypatch):
    monkeypatch.setattr(service, "MAX_BYTES", 8)
    monkeypatch.setattr(service, "MAX_TOTAL_BYTES", 12)
    too_large = client.post("/api/upload", files=[("files", ("large.txt", b"a" * 9, "text/plain"))])
    assert too_large.status_code == 413
    total = client.post(
        "/api/upload",
        files=[
            ("files", ("first.txt", b"a" * 7, "text/plain")),
            ("files", ("second.txt", b"b" * 6, "text/plain")),
        ],
    )
    assert total.status_code == 413
    assert not fake_repository.files
    assert not fake_repository.documents


def test_unknown_job_returns_404(client, fake_repository):
    job_id = uuid4()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/events").status_code == 404


def test_upload_registration_failure_cleans_storage_and_documents(
    client, fake_repository, monkeypatch
):
    async def unavailable(job_id, document_ids):
        raise RuntimeError("job RPC unavailable")

    monkeypatch.setattr(service.repository, "create_job", unavailable)
    response = client.post(
        "/api/upload", files=[("files", ("sample.txt", b"sample text", "text/plain"))]
    )
    assert response.status_code == 503
    assert not fake_repository.files
    assert not fake_repository.documents


def test_missing_job_store_fails_before_storage_write(client, fake_repository, monkeypatch):
    async def unavailable():
        raise RuntimeError("missing table")

    monkeypatch.setattr(service.repository, "check_job_store", unavailable)
    response = client.post(
        "/api/upload", files=[("files", ("sample.txt", b"sample text", "text/plain"))]
    )
    assert response.status_code == 503
    assert not fake_repository.files
    assert not fake_repository.documents


@pytest.mark.parametrize("status", ["queued", "running"])
def test_stale_job_becomes_persisted_failure(client, fake_repository, status):
    import asyncio

    job_id, document_id = uuid4(), uuid4()
    asyncio.run(fake_repository.create_job(job_id, [document_id]))
    fake_repository.jobs[str(job_id)]["status"] = status
    fake_repository.jobs[str(job_id)]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=31)
    ).isoformat()

    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["result"] == {"errorCode": "JOB_INTERRUPTED"}
    stream = client.get(f"/api/jobs/{job_id}/events")
    assert "event: failed" in stream.text
    assert '"errorCode": "JOB_INTERRUPTED"' in stream.text
    assert sum(row["event_type"] == "failed" for row in fake_repository.events) == 1


def test_recent_running_job_is_not_expired(client, fake_repository):
    import asyncio

    job_id = uuid4()
    asyncio.run(fake_repository.create_job(job_id, [uuid4()]))
    fake_repository.jobs[str(job_id)]["status"] = "running"
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_sse_keepalive_is_a_comment(fake_repository, monkeypatch):
    import asyncio

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
        assert "event: queued" in await anext(stream)
        assert await anext(stream) == ": keepalive\n\n"
        await fake_repository.record_event(
            job_id, "failed", {"errorCode": "JOB_INTERRUPTED"}, "failed"
        )
        assert "event: failed" in await anext(stream)

    asyncio.run(run())


def test_sse_delivers_expiration_after_initial_event_read(client, fake_repository, monkeypatch):
    import asyncio

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
    stream = client.get(f"/api/jobs/{job_id}/events")
    assert stream.status_code == 200
    assert "event: queued" in stream.text
    assert "event: failed" in stream.text
    assert '"errorCode": "JOB_INTERRUPTED"' in stream.text


def test_job_failure_is_persisted(fake_repository, monkeypatch):
    class Graph:
        async def astream(self, inputs, config=None, stream_mode=None, durability=None):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover

    monkeypatch.setattr(service, "get_graph", lambda **kwargs: Graph())
    document_id, job_id = uuid4(), uuid4()
    path = f"uploads/{document_id}.txt"
    fake_repository.files[path] = b"content"
    import asyncio

    async def run():
        await fake_repository.create_document(document_id, "sample.txt", 7, path)
        await fake_repository.create_job(job_id, [document_id])
        await service.run_job(job_id, [document_id])

    asyncio.run(run())
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
    document_ids = [uuid4(), uuid4()]
    job_id = uuid4()
    import asyncio

    async def run():
        for document_id in document_ids:
            path = f"uploads/{document_id}.txt"
            fake_repository.files[path] = b"content"
            await fake_repository.create_document(document_id, "sample.txt", 7, path)
        await fake_repository.create_job(job_id, document_ids)
        await service.run_job(job_id, document_ids)

    asyncio.run(run())
    result = fake_repository.jobs[str(job_id)]["result"]
    assert fake_repository.jobs[str(job_id)]["status"] == "partial"
    assert [item["status"] for item in result["documents"]] == ["completed", "failed"]
