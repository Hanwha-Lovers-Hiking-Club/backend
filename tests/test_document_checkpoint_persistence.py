import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

from app.config import settings
from app.domains.documents import service
from app.domains.documents.pipeline.checkpoints import load_job_state
from app.domains.documents.pipeline.execution import initialize
from tests.test_document_jobs import fake_repository as fake_repository
from tests.test_document_processing import Saver, graph
from tests.test_document_review_retries import ReviewOne


def install_graph(monkeypatch, *, model=None, saver=None):
    saver = saver or Saver()

    def begin(state):
        return initialize(
            {
                **state,
                "chunk_options": {"target_chars": 30},
                "processing_options": {"retry_delay_seconds": 0},
            }
        )

    def factory(**kwargs):
        return graph(model or ReviewOne(), saver=saver, initialize_node=begin, **kwargs)

    monkeypatch.setattr(service, "get_graph", factory)
    return saver


async def register(repository, content):
    document_id, job_id = uuid4(), uuid4()
    path = f"uploads/{document_id}.txt"
    data = content.encode()
    repository.files[path] = data
    await repository.create_document(document_id, "source.txt", len(data), path)
    await repository.create_job(job_id, [document_id])
    return document_id, job_id


def test_service_preserves_review_payload_after_connection_and_process_exit(
    fake_repository, monkeypatch
):
    saver = install_graph(monkeypatch)

    async def run():
        document_id, job_id = await register(
            fake_repository,
            "A stable development experience.\n\nREVIEW another experience.",
        )
        await service.run_job(job_id, [document_id])
        state = await load_job_state(job_id)
        assert len(state["review_drafts"]) == 1
        assert len(state["verified_drafts"]) == len(state["embeddings"]) == 1
        assert len(state["chunks"]) == len(state["chunk_progress"]) == 2
        assert state["review_drafts"][0].source_extraction.evidence_index
        assert not state["sources"][0].path.exists()
        job = fake_repository.jobs[str(job_id)]
        assert job["status"] == "partial" and job["result"]["savedCards"] == 1
        assert len(saver.requests) == 1
        return job_id

    job_id = asyncio.run(run())
    # 새 프로세스에서 같은 SQLite를 열어 메모리 객체가 아닌 영속 기록임을 검증한다.
    code = (
        "import asyncio,json; "
        "from app.domains.documents.pipeline.checkpoints import load_job_state; "
        f"state=asyncio.run(load_job_state('{job_id}')); "
        "print(json.dumps({'reviews':len(state['review_drafts']),'chunks':len(state['chunks'])}))"
    )
    env = {**os.environ, "DOCUMENT_CHECKPOINT_PATH": str(settings.DOCUMENT_CHECKPOINT_PATH)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == {"reviews": 1, "chunks": 2}


def test_success_cleanup_occurs_only_after_durable_terminal_result(fake_repository, monkeypatch):
    install_graph(monkeypatch)
    original = fake_repository.record_event
    observed = []

    async def record(job_id, event_type, payload, status=None):
        if status == "completed":
            state = await load_job_state(job_id)
            observed.append(state is not None)
            assert all(item.status == "completed" for item in state["document_progress"].values())
        return await original(job_id, event_type, payload, status)

    monkeypatch.setattr(service.repository, "record_event", record)

    async def run():
        document_id, job_id = await register(fake_repository, "A stable development experience.")
        await service.run_job(job_id, [document_id])
        assert fake_repository.jobs[str(job_id)]["status"] == "completed"
        assert fake_repository.jobs[str(job_id)]["result"]["savedCards"] == 1
        assert await load_job_state(job_id) is None

    asyncio.run(run())
    assert observed == [True]


def test_terminal_result_write_failure_does_not_delete_checkpoint(fake_repository, monkeypatch):
    install_graph(monkeypatch)
    original = fake_repository.record_event

    async def fail_terminal(job_id, event_type, payload, status=None):
        if status in service.TERMINAL:
            raise RuntimeError("Synthetic result write failure")
        return await original(job_id, event_type, payload, status)

    monkeypatch.setattr(service.repository, "record_event", fail_terminal)

    async def run():
        document_id, job_id = await register(fake_repository, "A stable development experience.")
        await service.run_job(job_id, [document_id])
        state = await load_job_state(job_id)
        assert state["document_progress"][str(document_id)].status == "completed"
        assert len(state["document_progress"][str(document_id)].saved_card_ids) == 1

    asyncio.run(run())


def test_successful_chunk_survives_later_unexpected_graph_failure(fake_repository, monkeypatch):
    class Broken(ReviewOne):
        async def generate(self, **kwargs):
            if "blocks" in kwargs["payload"] and any(
                "CRASH" in block["text"] for block in kwargs["payload"]["blocks"]
            ):
                raise TypeError("Synthetic code defect")
            return await super().generate(**kwargs)

    install_graph(monkeypatch, model=Broken())

    async def run():
        document_id, job_id = await register(
            fake_repository,
            "A stable development experience.\n\nCRASH the second experience.",
        )
        await service.run_job(job_id, [document_id])
        state = await load_job_state(job_id)
        assert sum(item.status == "succeeded" for item in state["chunk_progress"].values()) == 1
        assert fake_repository.jobs[str(job_id)]["status"] == "failed"

    asyncio.run(run())


def test_completed_job_cleanup_keeps_other_review_jobs(fake_repository, monkeypatch):
    install_graph(monkeypatch)

    async def run():
        review_document, review_job = await register(fake_repository, "REVIEW another experience.")
        await service.run_job(review_job, [review_document])
        complete_document, complete_job = await register(fake_repository, "A stable experience.")
        await service.run_job(complete_job, [complete_document])
        assert await load_job_state(complete_job) is None
        review = await load_job_state(review_job)
        assert len(review["review_drafts"]) == 1
        assert review["document_progress"][str(review_document)].status == "needs_review"

    asyncio.run(run())


def test_budget_failure_is_reported_before_paid_calls(fake_repository, monkeypatch):
    model = ReviewOne()
    install_graph(monkeypatch, model=model)

    async def run():
        document_id, job_id = await register(
            fake_repository,
            "\n\n".join(f"Independent development experience {i}." for i in range(60)),
        )
        await service.run_job(job_id, [document_id])
        job = fake_repository.jobs[str(job_id)]
        assert job["status"] == "failed" and job["result"]["errorCode"] == "JOB_TOO_LARGE"
        assert not model.calls

    asyncio.run(run())
