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
        self.jobs[str(job_id)] = {
            "id": str(job_id),
            "status": "queued",
            "document_ids": [str(item) for item in document_ids],
            "result": None,
            "created_at": "2026-10-31T00:00:00Z",
            "updated_at": "2026-10-31T00:00:00Z",
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

    async def get_job(self, job_id):
        return self.jobs.get(str(job_id))

    async def get_events(self, job_id, after_id=0):
        return [
            row for row in self.events if row["job_id"] == str(job_id) and row["id"] > after_id
        ][:100]

    async def get_documents(self, document_ids):
        return [self.documents[str(item)] for item in document_ids]


@pytest.fixture
def fake_repository(monkeypatch):
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
        "get_job",
        "get_events",
        "get_documents",
    ):
        monkeypatch.setattr(service.repository, name, getattr(fake, name))
    return fake


def test_upload_job_and_late_sse_reconnect(client, fake_repository, monkeypatch):
    runs = []

    class Graph:
        async def astream(self, inputs, stream_mode):
            assert stream_mode == "updates"
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

    monkeypatch.setattr(service, "get_graph", Graph)
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


def test_job_failure_is_persisted(fake_repository, monkeypatch):
    class Graph:
        async def astream(self, inputs, stream_mode):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover

    monkeypatch.setattr(service, "get_graph", Graph)
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
        async def astream(self, inputs, stream_mode):
            first = inputs["sources"][0].document_id
            yield {
                "complete_document": {
                    "document_progress": {
                        str(first): DocumentProgress(document_id=first, status="completed")
                    }
                }
            }
            raise RuntimeError("later document failed")

    monkeypatch.setattr(service, "get_graph", Graph)
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
