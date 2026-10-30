# GET /api/documents/{document_id}/url 테스트.
# repository를 가짜로 바꿔 실제 Supabase를 호출하지 않는다.
from uuid import uuid4

import pytest
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError

from app.domains.documents import repository

SIGNED_URL = "https://test.supabase.co/storage/v1/object/sign/documents/a.pdf?token=t"


def _url(document_id) -> str:
    return f"/api/documents/{document_id}/url"


class FakeRepository:
    def __init__(self, document: dict | None, fail_get=False, fail_sign=False):
        self.document = document
        self.fail_get = fail_get
        self.fail_sign = fail_sign
        self.signed: list[tuple[str, int, str | None]] = []

    def get_document(self, document_id: str) -> dict | None:
        if self.fail_get:
            raise APIError({"code": "500", "message": "db error"})
        return self.document

    def create_signed_url(self, path: str, expires_in: int, download: str | None = None) -> str:
        if self.fail_sign:
            raise StorageApiError("sign failed", "InternalError", 500)
        self.signed.append((path, expires_in, download))
        return SIGNED_URL


@pytest.fixture
def storage(monkeypatch):
    def install(filename: str | None = "설계서.pdf", **fails) -> FakeRepository:
        document = None
        if filename is not None:
            document = {
                "storage_path": f"job/uuid.{filename.rsplit('.', 1)[-1]}",
                "original_filename": filename,
            }
        fake = FakeRepository(document, **fails)
        monkeypatch.setattr(repository, "get_document", fake.get_document)
        monkeypatch.setattr(repository, "create_signed_url", fake.create_signed_url)
        return fake

    return install


def test_pdf_opens_in_browser(client, storage):
    fake = storage("설계서.pdf")

    response = client.get(_url(uuid4()))

    assert response.status_code == 200
    assert response.json() == {"url": SIGNED_URL, "expiresIn": 600}
    assert fake.signed == [("job/uuid.pdf", 600, None)]


def test_docx_downloads_with_original_filename(client, storage):
    fake = storage("회의록 v2.docx")

    client.get(_url(uuid4()))

    assert fake.signed == [("job/uuid.docx", 600, "회의록 v2.docx")]


def test_uppercase_pdf_extension_opens_in_browser(client, storage):
    fake = storage("REPORT.PDF")

    client.get(_url(uuid4()))

    assert fake.signed[0][2] is None


def test_unknown_document_returns_404(client, storage):
    fake = storage(None)

    response = client.get(_url(uuid4()))

    assert response.status_code == 404
    assert response.json() == {"detail": "문서를 찾을 수 없습니다."}
    assert fake.signed == []


def test_db_error_returns_500(client, storage):
    storage(fail_get=True)

    response = client.get(_url(uuid4()))

    assert response.status_code == 500
    assert response.json() == {"detail": "원본 문서 주소를 만들지 못했습니다."}


def test_storage_error_returns_500(client, storage):
    storage(fail_sign=True)

    response = client.get(_url(uuid4()))

    assert response.status_code == 500
    assert response.json() == {"detail": "원본 문서 주소를 만들지 못했습니다."}


def test_invalid_document_id_returns_422(client):
    response = client.get(_url("not-a-uuid"))

    assert response.status_code == 422
