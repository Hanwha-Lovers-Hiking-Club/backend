# /api/projects 테스트. 다른 도메인 호출과 DB는 가짜로 바꾼다.
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.domains.projects import service

PROJECT_A = "11111111-1111-1111-1111-111111111111"
PROJECT_B = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"
CREATED_AT = "2026-11-08T00:00:00+00:00"


class FakeQuery:
    def __init__(self, data=None, error=None):
        self.data, self.error = data or [], error
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            return self

        return call

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


@pytest.fixture
def fake_db(monkeypatch):
    def install(data=None, error=None) -> FakeQuery:
        query = FakeQuery(data, error)
        monkeypatch.setattr(service, "supabase", SimpleNamespace(table=lambda _name: query))
        return query

    return install


def _row(project_id: str, name: str, documents: int, cards: int) -> dict:
    return {
        "id": project_id,
        "name": name,
        "created_at": CREATED_AT,
        "documents": [{"count": documents}],
        "knowledge_cards": [{"count": cards}],
    }


def test_same_name_projects_stay_separate(client, fake_db):
    fake_db([_row(PROJECT_A, "말결화면", 6, 4), _row(PROJECT_B, "말결화면", 2, 0)])

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": PROJECT_A,
            "name": "말결화면",
            "documentCount": 6,
            "cardCount": 4,
            "createdAt": "2026-11-08T00:00:00Z",
        },
        {
            "id": PROJECT_B,
            "name": "말결화면",
            "documentCount": 2,
            "cardCount": 0,
            "createdAt": "2026-11-08T00:00:00Z",
        },
    ]


def test_project_list_counts_only_active_cards(client, fake_db):
    query = fake_db([])

    client.get("/api/projects")

    assert ("eq", ("knowledge_cards.status", service.ACTIVE_STATUS)) in query.calls


def test_project_list_failure_is_500(client, fake_db):
    fake_db(error=APIError({"code": "500", "message": "x"}))

    response = client.get("/api/projects")

    assert response.status_code == 500
    assert response.json() == {"detail": service.LIST_FAILED_DETAIL}


def test_project_detail_has_documents(client, fake_db, monkeypatch):
    fake_db([{"id": PROJECT_A, "name": "말결화면", "created_at": CREATED_AT}])
    document = {
        "id": DOCUMENT_ID,
        "title": "설계서",
        "original_filename": "설계서.pdf",
        "size_bytes": 10,
        "created_at": CREATED_AT,
    }
    monkeypatch.setattr(
        service.documents_repository, "list_project_documents", lambda _id: [document]
    )
    monkeypatch.setattr(service.knowledge_cards_service, "list_project_cards", lambda _id: [])

    response = client.get(f"/api/projects/{PROJECT_A}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "말결화면" and body["cards"] == []
    assert body["documents"][0]["originalFilename"] == "설계서.pdf"


def test_unknown_project_is_404(client, fake_db):
    fake_db([])

    response = client.get(f"/api/projects/{PROJECT_A}")

    assert response.status_code == 404
    assert response.json() == {"detail": service.PROJECT_NOT_FOUND_DETAIL}
