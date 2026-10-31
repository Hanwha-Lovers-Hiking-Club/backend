from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.domains.knowledge_cards import service

CARD_ID = "8d482ad2-63a9-42f4-a9fb-fab341ef6d25"
EVIDENCE_ID = "0b9a5c1e-1f0d-4d4b-9a51-3c7a3f0e8b21"
DOCUMENT_ID = "4c10a229-9b3a-40bc-8611-e2f787540f03"

CARD_ROW = {
    "id": CARD_ID,
    "title": "RAG 검색 정확도 개선",
    "summary": "하이브리드 검색 도입 사례",
    "context": "벡터 검색만으로 정확한 기술 용어 검색이 어려웠다.",
    "problem": "특정 키워드 검색 실패",
    "cause": None,
    "approach": "키워드 검색과 벡터 검색 결합",
    "rationale": None,
    "outcome": None,
    "lesson": "검색 유형에 따라 검색 방식을 결합해야 한다.",
    "technologies": ["Supabase", "pgvector"],
    "keywords": ["RAG", "Hybrid Search"],
    "department": None,
    "project_date": "2026-10-20",
    "project_name": "AI Knowledge Platform",
    "status": "active",
    "quality_score": 0.92,
    "last_verified_at": None,
    "verification_version": None,
    "created_at": "2026-10-20T09:00:00+00:00",
    "updated_at": "2026-10-24T09:00:00+00:00",
    "card_documents": [
        {
            "id": EVIDENCE_ID,
            "document_id": DOCUMENT_ID,
            "location": "3장 검색 방식",
            "excerpt": "벡터 검색과 키워드 검색을 결합한다.",
            "documents": {"title": "검색 시스템 설계서", "original_filename": "search-design.pdf"},
        }
    ],
}


class FakeQuery:
    """supabase 쿼리 빌더 대역. 호출된 체인 메서드를 기록하고 정해둔 결과를 차례로 돌려준다.
    결과가 예외면 execute()에서 그 예외를 던진다."""

    def __init__(self, results):
        self.calls = []
        self._results = results

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return method

    def execute(self):
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def called(self, name):
        return [(args, kwargs) for n, args, kwargs in self.calls if n == name]


@pytest.fixture
def fake_db(monkeypatch):
    def install(data=None, count=0, fail_with=None):
        # fail_with를 주면 첫 execute()는 그 예외를 던지고, 다음 execute()가 data/count를 돌려준다
        results = [SimpleNamespace(data=data or [], count=count)]
        if fail_with:
            results.insert(0, fail_with)
        query = FakeQuery(results)
        monkeypatch.setattr(service, "supabase", SimpleNamespace(table=lambda name: query))
        return query

    return install


def test_list_cards_returns_camel_case_cards_with_evidences(client, fake_db):
    fake_db(data=[dict(CARD_ROW)], count=1)

    response = client.get("/api/knowledge/cards")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    card = body["items"][0]
    assert card["id"] == CARD_ID
    assert card["qualityScore"] == 0.92
    assert "card_documents" not in card
    assert card["evidences"] == [
        {
            "id": EVIDENCE_ID,
            "documentId": DOCUMENT_ID,
            "documentTitle": "검색 시스템 설계서",
            "originalFilename": "search-design.pdf",
            "location": "3장 검색 방식",
            "excerpt": "벡터 검색과 키워드 검색을 결합한다.",
            "url": None,
        }
    ]


def test_list_cards_default_query(client, fake_db):
    query = fake_db()

    response = client.get("/api/knowledge/cards")

    assert response.json() == {"items": [], "total": 0}
    assert query.called("eq") == [(("status", "active"), {})]
    assert query.called("or_") == []
    assert query.called("order") == [(("updated_at",), {"desc": True}), (("id",), {})]
    assert query.called("range") == [((0, 11), {})]


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        ("recent", (("updated_at",), {"desc": True})),
        ("created", (("created_at",), {"desc": True})),
        ("name", (("title",), {"desc": False})),
    ],
)
def test_list_cards_sort(client, fake_db, sort, order):
    query = fake_db()

    client.get("/api/knowledge/cards", params={"sort": sort})

    assert query.called("order")[0] == order


def test_list_cards_pagination(client, fake_db):
    query = fake_db()

    client.get("/api/knowledge/cards", params={"page": 3, "pageSize": 5})

    assert query.called("range") == [((10, 14), {})]


def test_list_cards_search_filter(client, fake_db):
    query = fake_db()

    client.get("/api/knowledge/cards", params={"q": "  RAG  "})

    assert query.called("or_") == [
        (
            (
                'title.ilike."*RAG*",summary.ilike."*RAG*",context.ilike."*RAG*",'
                'keywords.cs.{"RAG"},technologies.cs.{"rag"}',
            ),
            {},
        )
    ]


def test_list_cards_blank_search_is_ignored(client, fake_db):
    query = fake_db()

    client.get("/api/knowledge/cards", params={"q": "   "})

    assert query.called("or_") == []


def test_search_filter_escapes_reserved_characters():
    assert service._build_search_filter('a,"b"') == (
        r'title.ilike."*a,\"b\"*",summary.ilike."*a,\"b\"*",context.ilike."*a,\"b\"*",'
        r'keywords.cs.{"a,\"b\""}'
    )


@pytest.mark.parametrize(
    "params",
    [{"sort": "popular"}, {"page": 0}, {"pageSize": 0}, {"pageSize": 101}, {"page": "abc"}],
)
def test_list_cards_invalid_params(client, fake_db, params):
    fake_db()

    response = client.get("/api/knowledge/cards", params=params)

    assert response.status_code == 422
    assert "detail" in response.json()


def test_list_cards_page_out_of_range_returns_total(client, fake_db):
    query = fake_db(
        count=7,
        fail_with=APIError({"code": "PGRST103", "message": "Requested range not satisfiable"}),
    )

    response = client.get("/api/knowledge/cards", params={"page": 999, "q": "RAG"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 7}
    # count 재조회도 본 조회와 같은 조건(active + 검색어)을 쓴다
    assert query.called("eq") == [(("status", "active"), {})] * 2
    assert len(query.called("or_")) == 2


def test_list_cards_db_error(client, fake_db):
    fake_db(fail_with=APIError({"code": "42703", "message": "column does not exist"}))

    response = client.get("/api/knowledge/cards")

    assert response.status_code == 500
    assert response.json() == {"detail": "지식카드 목록을 조회하지 못했습니다."}


def test_search_filter_matches_technologies_by_standard_id():
    # 입력이 별칭("넥스트")이어도 저장된 표준 ID(nextjs)로 비교한다. 조사가 붙어도 찾는다
    condition = "technologies.cs.{" + '"nextjs"' + "}"
    assert condition in service._build_search_filter("넥스트 장애").split(",")
    assert condition in service._build_search_filter("넥스트에서 장애").split(",")
    # 사전에 없는 기술도 같은 철자로 맞는다
    assert 'technologies.cs.{"somenewtool"}' in service._build_search_filter("SomeNewTool")


def test_technologies_are_returned_with_display_names():
    row = {"id": "1", "technologies": ["nextjs", "Supabase", "unknowntool"], "card_documents": []}
    assert service._to_card(row)["technologies"] == ["Next.js", "Supabase", "unknowntool"]
