# POST /api/documents/upload 테스트. Storage 호출(repository)은 가짜로 바꿔 실제로 올리지 않는다.
import re
from uuid import UUID

import pytest
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError

from app.domains.documents import jobs, repository, service

URL = "/api/documents/upload"
PROJECT_ID = "11111111-1111-1111-1111-111111111111"


class FakeRepository:
    def __init__(
        self,
        fail_on: int | None = None,
        fail_insert: bool = False,
        fail_project: bool = False,
    ):
        self.projects: list[str] = []
        self.deleted_projects: list[str] = []
        self.fail_project = fail_project
        self.uploads: list[tuple[str, bytes, str]] = []
        self.removed: list[list[str]] = []
        self.inserted: list[dict] = []
        # 몇 번째(0부터) 업로드에서 실패할지
        self.fail_on = fail_on
        self.fail_insert = fail_insert

    def create_project(self, name: str) -> str:
        if self.fail_project:
            raise APIError({"code": "500", "message": "project insert failed"})
        self.projects.append(name)
        return PROJECT_ID

    def delete_project(self, project_id: str) -> None:
        self.deleted_projects.append(project_id)

    def upload_file(self, path: str, content: bytes, content_type: str) -> None:
        if len(self.uploads) == self.fail_on:
            raise StorageApiError("upload failed", "InternalError", 500)
        self.uploads.append((path, content, content_type))

    def remove_files(self, paths: list[str]) -> None:
        self.removed.append(paths)

    def insert_documents(self, rows: list[dict]) -> list[str]:
        if self.fail_insert:
            raise APIError({"code": "23514", "message": "check constraint violated"})
        self.inserted.extend(rows)
        return [f"doc-{i}" for i in range(len(rows))]


@pytest.fixture(autouse=True)
def clear_jobs():
    jobs._jobs.clear()


@pytest.fixture(autouse=True)
def started(monkeypatch) -> list:
    # 업로드 테스트는 파이프라인을 실제로 띄우지 않고, 시작 요청만 기록한다
    calls: list[tuple] = []
    monkeypatch.setattr(service, "_start_pipeline", lambda *args: calls.append(args))
    return calls


@pytest.fixture
def storage(monkeypatch):
    def install(
        fail_on: int | None = None, fail_insert: bool = False, fail_project: bool = False
    ) -> FakeRepository:
        fake = FakeRepository(fail_on, fail_insert, fail_project)
        monkeypatch.setattr(service.projects_service, "create_project", fake.create_project)
        monkeypatch.setattr(service.projects_service, "delete_project", fake.delete_project)
        monkeypatch.setattr(repository, "upload_file", fake.upload_file)
        monkeypatch.setattr(repository, "remove_files", fake.remove_files)
        monkeypatch.setattr(repository, "insert_documents", fake.insert_documents)
        return fake

    return install


def _file(name: str, content: bytes = b"data"):
    return ("files", (name, content, "application/octet-stream"))


def test_upload_returns_job(client, storage, started):
    fake = storage()

    response = client.post(URL, files=[_file("a.pdf"), _file("b.docx"), _file("c.md")])

    assert response.status_code == 202
    body = response.json()
    assert body["projectId"] == PROJECT_ID
    assert body["documentCount"] == 3
    assert body["status"] == "queued"
    documents = [
        {
            "id": f"doc-{i}",
            "storage_bucket": "documents",
            "storage_path": fake.uploads[i][0],
            "original_filename": name,
        }
        for i, name in enumerate(["a.pdf", "b.docx", "c.md"])
    ]
    job_id = UUID(body["jobId"])
    assert jobs._jobs[job_id] == {
        "status": "queued",
        "document_count": 3,
        "documents": documents,
        "active_step": 0,
        "progress": service.STEP_PROGRESS[0],
    }
    # 그래프에 넘길 문서 정보와 함께 파이프라인 시작이 요청된다
    assert started == [(job_id, PROJECT_ID, documents)]


def test_upload_inserts_document_rows(client, storage):
    fake = storage()

    client.post(URL, files=[_file("검색 설계서.v2.pdf", b"12345"), _file("README.md", b"# a")])

    pdf_row, md_row = fake.inserted
    assert pdf_row == {
        "title": "검색 설계서.v2",
        "original_filename": "검색 설계서.v2.pdf",
        "size_bytes": 5,
        "project_id": PROJECT_ID,
        "storage_bucket": "documents",
        "storage_path": fake.uploads[0][0],
    }
    assert (md_row["title"], md_row["storage_path"]) == ("README", fake.uploads[1][0])


def test_upload_insert_failure_removes_stored_files(client, storage, started):
    fake = storage(fail_insert=True)

    response = client.post(URL, files=[_file("a.pdf"), _file("b.md")])

    assert response.status_code == 500
    assert response.json() == {"detail": "문서를 저장하지 못했습니다."}
    assert fake.removed == [[path for path, _, _ in fake.uploads]]
    assert fake.deleted_projects == [PROJECT_ID]
    assert jobs._jobs == {}
    assert started == []


def test_project_failure_removes_stored_files(client, storage, started):
    fake = storage(fail_project=True)

    response = client.post(URL, files=[_file("a.pdf"), _file("b.md")])

    assert response.status_code == 500
    assert fake.removed == [[path for path, _, _ in fake.uploads]]
    assert fake.inserted == [] and started == []


def test_temporary_project_name_is_first_file_by_name(client, storage):
    fake = storage()

    client.post(URL, files=[_file("b 문서.pdf"), _file("a 문서.v2.md"), _file("c.txt")])

    assert fake.projects == ["a 문서.v2"]


def test_documents_are_stored_with_the_new_project(client, storage):
    fake = storage()

    client.post(URL, files=[_file("a.pdf"), _file("b.md")])

    assert {row["project_id"] for row in fake.inserted} == {PROJECT_ID}


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


def test_upload_rejects_unsupported_type(client, storage, started):
    fake = storage()

    response = client.post(URL, files=[_file("a.pdf"), _file("slides.ppt")])

    assert response.status_code == 400
    assert response.json() == {"detail": "지원하지 않는 파일 형식입니다: slides.ppt"}
    assert fake.uploads == []
    assert jobs._jobs == {}
    assert started == []


def test_upload_rejects_too_large_file(client, storage, monkeypatch):
    fake = storage()
    monkeypatch.setattr(service, "MAX_FILE_SIZE", 3)

    response = client.post(URL, files=[_file("big.pdf", b"1234")])

    assert response.status_code == 413
    assert response.json() == {"detail": "파일 크기가 50MB를 넘습니다: big.pdf"}
    assert fake.uploads == []
    assert jobs._jobs == {}


def test_upload_failure_removes_stored_files(client, storage):
    fake = storage(fail_on=1)

    response = client.post(URL, files=[_file("a.pdf"), _file("b.pdf")])

    assert response.status_code == 500
    assert response.json() == {"detail": "문서를 저장하지 못했습니다."}
    assert fake.removed == [[fake.uploads[0][0]]]
    assert jobs._jobs == {}


def test_upload_failure_on_first_file_removes_nothing(client, storage):
    fake = storage(fail_on=0)

    response = client.post(URL, files=[_file("a.pdf")])

    assert response.status_code == 500
    assert fake.removed == []


def test_insert_failure_logs_original_error_after_cleanup(client, storage, caplog):
    # 파일 정리를 스레드에서 기다린 뒤에도 삭제가 아니라 원래 insert 에러가 로그에 남아야 한다
    fake = storage(fail_insert=True)
    caplog.set_level("ERROR", logger="app")

    response = client.post(URL, files=[_file("a.pdf")])

    assert response.status_code == 500
    assert fake.removed == [[fake.uploads[0][0]]]
    assert [record.exc_info[0] for record in caplog.records] == [APIError]


def test_upload_requires_files(client):
    response = client.post(URL)

    assert response.status_code == 422
