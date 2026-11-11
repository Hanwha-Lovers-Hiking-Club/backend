# /api/projects 테스트. 다른 도메인 호출과 DB는 가짜로 바꾼다.
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.domains.projects import service

PROJECT_A = "11111111-1111-1111-1111-111111111111"
PROJECT_B = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"
CREATED_AT = "2026-11-08T00:00:00+00:00"
META = {
    "domain": "frontend",
    "team": "국제화제품팀",
    "period": "2026.01 – 2026.04",
    "description": "다국어 화면 정비",
    "stack": ["i18n", "CI"],
}
EMPTY_META = {"domain": None, "team": None, "period": None, "description": None, "stack": []}


class FakeQuery:
    def __init__(self, data=None, error=None, count=None):
        self.data, self.error = data or [], error
        self.count = len(self.data) if count is None else count
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            return self

        return call

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data, count=self.count)


@pytest.fixture
def fake_db(monkeypatch):
    def install(data=None, error=None, count=None) -> FakeQuery:
        query = FakeQuery(data, error, count)
        monkeypatch.setattr(service, "supabase", SimpleNamespace(table=lambda _name: query))
        return query

    return install


def _row(project_id: str, name: str, documents: int, cards: int, meta=None) -> dict:
    return {
        **(meta or EMPTY_META),
        "id": project_id,
        "name": name,
        "created_at": CREATED_AT,
        "documents": [{"count": documents}],
        "knowledge_cards": [{"count": cards}],
    }


def test_same_name_projects_stay_separate(client, fake_db):
    fake_db([_row(PROJECT_A, "말결화면", 6, 4, META), _row(PROJECT_B, "말결화면", 2, 0)])

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["items"] == [
        {
            "id": PROJECT_A,
            "name": "말결화면",
            "documentCount": 6,
            "cardCount": 4,
            "createdAt": "2026-11-08T00:00:00Z",
            **META,
        },
        {
            "id": PROJECT_B,
            "name": "말결화면",
            "documentCount": 2,
            "cardCount": 0,
            "createdAt": "2026-11-08T00:00:00Z",
            **EMPTY_META,
        },
    ]


UNFINISHED_HIDDEN = ("filter", ("completed_at", "not.is", "null"))


@pytest.mark.parametrize("params", [{}, {"q": "말결"}])
def test_project_list_hides_unfinished_projects(client, fake_db, params):
    # 분석 중이거나 실패한 프로젝트는 이름·설명이 비어 있어서 목록에서 뺀다
    query = fake_db([])

    client.get("/api/projects", params=params)

    assert UNFINISHED_HIDDEN in query.calls


def test_mark_completed_saves_completion_time(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(service, "update_project", lambda *args: calls.append(args))

    service.mark_completed(PROJECT_A)

    [(project_id, fields)] = calls
    assert project_id == PROJECT_A and set(fields) == {"completed_at"}


def test_project_list_counts_only_active_cards(client, fake_db):
    query = fake_db([])

    client.get("/api/projects")

    assert ("eq", ("knowledge_cards.status", service.ACTIVE_STATUS)) in query.calls


def test_project_search_sort_and_page_are_applied_in_database(client, fake_db):
    query = fake_db([_row(PROJECT_A, "말결화면", 6, 4)], count=25)

    response = client.get(
        "/api/projects",
        params={
            "q": "  말결  ",
            "sort": "name",
            "page": 2,
            "pageSize": 12,
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 25
    assert len(response.json()["items"]) == 1
    assert (
        "or_",
        (
            'name.ilike."*말결*",description.ilike."*말결*",'
            'team.ilike."*말결*",stack_text.ilike."*말결*"',
        ),
    ) in query.calls
    assert ("order", ("name",)) in query.calls
    assert ("order", ("id",)) in query.calls
    assert ("range", (12, 23)) in query.calls


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"pageSize": 0},
        {"pageSize": 101},
        {"sort": "invalid"},
    ],
)
def test_invalid_project_list_parameters_are_rejected(client, fake_db, params):
    query = fake_db()
    assert client.get("/api/projects", params=params).status_code == 422
    assert query.calls == []


def test_project_page_out_of_range_keeps_filtered_total(client, monkeypatch):
    page = FakeQuery(error=APIError({"code": "PGRST103", "message": "out of range"}))
    count = FakeQuery(count=3)
    queries = iter([page, count])
    monkeypatch.setattr(service, "supabase", SimpleNamespace(table=lambda _: next(queries)))

    response = client.get("/api/projects", params={"q": "말결", "page": 99})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 3}
    assert UNFINISHED_HIDDEN in count.calls
    assert (
        "or_",
        (
            'name.ilike."*말결*",description.ilike."*말결*",'
            'team.ilike."*말결*",stack_text.ilike."*말결*"',
        ),
    ) in count.calls


def test_project_page_out_of_range_count_failure_is_500(client, monkeypatch):
    page = FakeQuery(error=APIError({"code": "PGRST103", "message": "out of range"}))
    count = FakeQuery(error=APIError({"code": "500", "message": "db down"}))
    queries = iter([page, count])
    monkeypatch.setattr(service, "supabase", SimpleNamespace(table=lambda _: next(queries)))

    assert client.get("/api/projects", params={"page": 99}).status_code == 500


def test_project_list_failure_is_500(client, fake_db):
    fake_db(error=APIError({"code": "500", "message": "x"}))

    response = client.get("/api/projects")

    assert response.status_code == 500
    assert response.json() == {"detail": service.LIST_FAILED_DETAIL}


def test_project_detail_has_documents(client, fake_db, monkeypatch):
    fake_db([{"id": PROJECT_A, "name": "말결화면", "created_at": CREATED_AT, **META}])
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
    assert {key: body[key] for key in META} == META
    assert body["documents"][0]["originalFilename"] == "설계서.pdf"


def test_unknown_project_is_404(client, fake_db):
    fake_db([])

    response = client.get(f"/api/projects/{PROJECT_A}")

    assert response.status_code == 404
    assert response.json() == {"detail": service.PROJECT_NOT_FOUND_DETAIL}


@pytest.mark.parametrize(
    ("q", "domains"),
    [
        ("프론트엔드", ["frontend"]),
        ("백엔드에서 쓴 기술", ["backend"]),
        ("AI", ["ai"]),
        ("인공지능 에이전트", ["ai"]),
        ("데이터 정합성", ["data"]),
        ("모바일 앱", ["app"]),
        ("Frontend", ["frontend"]),
        # 영문 코드는 단어 단위로만 비교한다
        ("email", []),
        ("말결", []),
    ],
)
def test_search_domains_from_query(q, domains):
    assert service._search_domains(q) == domains


def test_project_search_includes_matching_domain(client, fake_db):
    query = fake_db([])

    client.get("/api/projects", params={"q": "프론트엔드"})

    assert (
        "or_",
        (
            'name.ilike."*프론트엔드*",description.ilike."*프론트엔드*",'
            'team.ilike."*프론트엔드*",stack_text.ilike."*프론트엔드*",domain.eq.frontend',
        ),
    ) in query.calls
