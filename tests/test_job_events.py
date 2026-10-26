# GET /api/documents/jobs/{job_id}/events (SSE) 테스트.
# 작업 상태를 service._jobs에 직접 넣고, 대기(_wait)를 가짜로 바꿔 대기할 때마다 상태를 바꾼다.
from uuid import uuid4

import pytest

from app.domains.documents import service


@pytest.fixture(autouse=True)
def clear_jobs():
    service._jobs.clear()


def _url(job_id) -> str:
    return f"/api/documents/jobs/{job_id}/events"


def _job(**fields) -> dict:
    return {"status": "queued", "active_step": 0, "progress": 16, **fields}


def _install_changes(monkeypatch, job_id, changes: list[dict]):
    # _wait가 불릴 때마다 changes를 하나씩 작업 상태에 반영한다
    remaining = list(changes)

    async def fake_wait():
        service._jobs[job_id].update(remaining.pop(0))

    monkeypatch.setattr(service, "_wait", fake_wait)


def test_streams_progress_until_completed(client, monkeypatch):
    job_id = uuid4()
    service._jobs[job_id] = _job()
    _install_changes(
        monkeypatch,
        job_id,
        [
            {"status": "running", "active_step": 1, "progress": 32},
            # 상태가 그대로면 다시 보내지 않는다
            {},
            {"active_step": 2, "progress": 48},
            {"status": "completed", "created_card_count": 8},
        ],
    )

    response = client.get(_url(job_id))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == (
        'event: progress\ndata: {"activeStep":0,"progress":16}\n\n'
        'event: progress\ndata: {"activeStep":1,"progress":32}\n\n'
        'event: progress\ndata: {"activeStep":2,"progress":48}\n\n'
        f'event: completed\ndata: {{"jobId":"{job_id}","status":"completed",'
        '"createdCardCount":8}\n\n'
    )


def test_completed_job_sends_only_completed(client):
    job_id = uuid4()
    service._jobs[job_id] = _job(status="completed", created_card_count=3)

    response = client.get(_url(job_id))

    assert response.text == (
        f'event: completed\ndata: {{"jobId":"{job_id}","status":"completed",'
        '"createdCardCount":3}\n\n'
    )


def test_failed_job_sends_failed(client):
    job_id = uuid4()
    service._jobs[job_id] = _job(status="failed", message="문서를 읽지 못했습니다.")

    response = client.get(_url(job_id))

    assert response.text == (
        f'event: failed\ndata: {{"jobId":"{job_id}","message":"문서를 읽지 못했습니다."}}\n\n'
    )


def test_unknown_job_returns_404(client):
    response = client.get(_url(uuid4()))

    assert response.status_code == 404
    assert response.json() == {"detail": "작업을 찾을 수 없습니다."}


def test_invalid_job_id_returns_422(client):
    response = client.get(_url("not-a-uuid"))

    assert response.status_code == 422
