# POST /api/documents/upload 테스트. 아직 Storage/DB를 쓰지 않아서 Supabase 대체가 필요 없다.
from uuid import UUID

import pytest

from app.domains.documents import service

URL = "/api/documents/upload"


@pytest.fixture(autouse=True)
def clear_jobs():
    service._jobs.clear()


def _file(name: str, content: bytes = b"data"):
    return ("files", (name, content, "application/octet-stream"))


def test_upload_returns_job(client):
    response = client.post(URL, files=[_file("a.pdf"), _file("b.docx"), _file("c.md")])

    assert response.status_code == 202
    body = response.json()
    assert body["documentCount"] == 3
    assert body["status"] == "queued"
    assert service._jobs[UUID(body["jobId"])] == {"status": "queued", "document_count": 3}


def test_upload_accepts_uppercase_extension(client):
    response = client.post(URL, files=[_file("REPORT.PDF")])

    assert response.status_code == 202


def test_upload_rejects_unsupported_type(client):
    response = client.post(URL, files=[_file("a.pdf"), _file("sheet.xlsx")])

    assert response.status_code == 400
    assert response.json() == {"detail": "지원하지 않는 파일 형식입니다: sheet.xlsx"}
    assert service._jobs == {}


def test_upload_rejects_too_large_file(client, monkeypatch):
    monkeypatch.setattr(service, "MAX_FILE_SIZE", 3)

    response = client.post(URL, files=[_file("big.pdf", b"1234")])

    assert response.status_code == 413
    assert response.json() == {"detail": "파일 크기가 20MB를 넘습니다: big.pdf"}
    assert service._jobs == {}


def test_upload_requires_files(client):
    response = client.post(URL)

    assert response.status_code == 422
