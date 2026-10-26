# POST /api/documents/upload 테스트. Storage 호출(repository)은 가짜로 바꿔 실제로 올리지 않는다.
import re
from uuid import UUID

import pytest
from storage3.exceptions import StorageApiError

from app.domains.documents import repository, service

URL = "/api/documents/upload"


class FakeStorage:
    def __init__(self, fail_on: int | None = None):
        self.uploads: list[tuple[str, bytes, str]] = []
        self.removed: list[list[str]] = []
        # 몇 번째(0부터) 업로드에서 실패할지
        self.fail_on = fail_on

    def upload_file(self, path: str, content: bytes, content_type: str) -> None:
        if len(self.uploads) == self.fail_on:
            raise StorageApiError("upload failed", "InternalError", 500)
        self.uploads.append((path, content, content_type))

    def remove_files(self, paths: list[str]) -> None:
        self.removed.append(paths)


@pytest.fixture(autouse=True)
def clear_jobs():
    service._jobs.clear()


@pytest.fixture
def storage(monkeypatch):
    def install(fail_on: int | None = None) -> FakeStorage:
        fake = FakeStorage(fail_on)
        monkeypatch.setattr(repository, "upload_file", fake.upload_file)
        monkeypatch.setattr(repository, "remove_files", fake.remove_files)
        return fake

    return install


def _file(name: str, content: bytes = b"data"):
    return ("files", (name, content, "application/octet-stream"))


def test_upload_returns_job(client, storage):
    storage()

    response = client.post(URL, files=[_file("a.pdf"), _file("b.docx"), _file("c.md")])

    assert response.status_code == 202
    body = response.json()
    assert body["documentCount"] == 3
    assert body["status"] == "queued"
    assert service._jobs[UUID(body["jobId"])] == {"status": "queued", "document_count": 3}


def test_upload_stores_files_under_job(client, storage):
    fake = storage()

    response = client.post(URL, files=[_file("설계서.pdf", b"pdf"), _file("NOTES.MD", b"# md")])

    job_id = response.json()["jobId"]
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    (pdf_path, pdf_content, pdf_type), (md_path, md_content, md_type) = fake.uploads
    assert re.fullmatch(rf"{job_id}/{uuid}\.pdf", pdf_path)
    assert re.fullmatch(rf"{job_id}/{uuid}\.md", md_path)
    assert (pdf_content, pdf_type) == (b"pdf", "application/pdf")
    # 브라우저가 octet-stream으로 보내도 확장자 기준으로 저장한다
    assert (md_content, md_type) == (b"# md", "text/markdown")


def test_upload_accepts_uppercase_extension(client, storage):
    storage()

    response = client.post(URL, files=[_file("REPORT.PDF")])

    assert response.status_code == 202


def test_upload_rejects_unsupported_type(client, storage):
    fake = storage()

    response = client.post(URL, files=[_file("a.pdf"), _file("sheet.xlsx")])

    assert response.status_code == 400
    assert response.json() == {"detail": "지원하지 않는 파일 형식입니다: sheet.xlsx"}
    assert fake.uploads == []
    assert service._jobs == {}


def test_upload_rejects_too_large_file(client, storage, monkeypatch):
    fake = storage()
    monkeypatch.setattr(service, "MAX_FILE_SIZE", 3)

    response = client.post(URL, files=[_file("big.pdf", b"1234")])

    assert response.status_code == 413
    assert response.json() == {"detail": "파일 크기가 50MB를 넘습니다: big.pdf"}
    assert fake.uploads == []
    assert service._jobs == {}


def test_upload_failure_removes_stored_files(client, storage):
    fake = storage(fail_on=1)

    response = client.post(URL, files=[_file("a.pdf"), _file("b.pdf")])

    assert response.status_code == 500
    assert response.json() == {"detail": "문서를 저장하지 못했습니다."}
    assert fake.removed == [[fake.uploads[0][0]]]
    assert service._jobs == {}


def test_upload_failure_on_first_file_removes_nothing(client, storage):
    fake = storage(fail_on=0)

    response = client.post(URL, files=[_file("a.pdf")])

    assert response.status_code == 500
    assert fake.removed == []


def test_upload_requires_files(client):
    response = client.post(URL)

    assert response.status_code == 422
