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
            "documentId": DOCUMENT_ID,
            "documentTitle": "검색 시스템 설계서",
            "originalFilename": "search-design.pdf",
            "url": None,
            "items": [
                {
                    "id": EVIDENCE_ID,
                    "location": "3장 검색 방식",
                    "excerpt": "벡터 검색과 키워드 검색을 결합한다.",
                }
            ],
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
                'keywords_text.ilike."*RAG*",technologies_text.ilike."*rag*"',
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
        r'keywords_text.ilike."*a,\"b\"*"'
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
    condition = 'technologies_text.ilike."*nextjs*"'
    assert condition in service._build_search_filter("넥스트 장애").split(",")
    assert condition in service._build_search_filter("넥스트에서 장애").split(",")
    # 사전에 없는 기술도 같은 철자로 맞는다
    assert 'technologies_text.ilike."*somenewtool*"' in service._build_search_filter("SomeNewTool")


def test_search_filter_matches_part_of_keywords_and_technologies():
    # 배열을 합친 생성 컬럼을 ilike로 찾아서, 원소의 일부만 입력해도 맞는다
    conditions = service._build_search_filter("제어").split(",")
    assert 'keywords_text.ilike."*제어*"' in conditions

    # 기술 스택은 표준 ID의 일부로 찾는다("post" -> postgresql). 대문자로 입력해도 같다
    assert 'technologies_text.ilike."*post*"' in service._build_search_filter("Post").split(",")


def test_search_filter_no_longer_requires_exact_array_elements():
    # 배열 원소 전체가 같아야 하는 조건(cs)이 남아 있으면 부분 일치가 다시 깨진다
    assert ".cs." not in service._build_search_filter("넥스트 장애")


def test_technologies_are_returned_with_display_names():
    row = {"id": "1", "technologies": ["nextjs", "Supabase", "unknowntool"], "card_documents": []}
    assert service._to_card(row)["technologies"] == ["Next.js", "Supabase", "unknowntool"]


def _evidence_row(evidence_id, document_id, location, excerpt="발췌문", title="문서"):
    return {
        "id": evidence_id,
        "document_id": document_id,
        "location": location,
        "excerpt": excerpt,
        "documents": {"title": title, "original_filename": f"{document_id}.pdf"},
    }


def test_evidences_of_same_document_are_grouped_without_losing_excerpts():
    row = {
        "id": "1",
        "card_documents": [
            _evidence_row("e1", "doc-a", "p.1", "첫 번째 발췌문"),
            _evidence_row("e2", "doc-a", "p.2", "두 번째 발췌문"),
            # 위치가 같아도 발췌문이 다르면 서로 다른 근거라 둘 다 남긴다
            _evidence_row("e3", "doc-a", "p.2", "세 번째 발췌문"),
        ],
    }

    [document] = service._to_card(row)["evidences"]

    assert document["document_id"] == "doc-a"
    assert document["original_filename"] == "doc-a.pdf"
    assert document["url"] is None
    # 위치가 같으면 발췌문 순서로 정렬한다
    assert [item["excerpt"] for item in document["items"]] == [
        "첫 번째 발췌문",
        "두 번째 발췌문",
        "세 번째 발췌문",
    ]
    assert [item["id"] for item in document["items"]] == ["e1", "e2", "e3"]


def test_evidences_of_different_documents_are_separate_and_sorted_by_title():
    row = {
        "id": "1",
        "card_documents": [
            _evidence_row("e1", "doc-b", "p.1", title="나 문서"),
            _evidence_row("e2", "doc-a", "p.1", title="가 문서"),
            _evidence_row("e3", "doc-b", "p.2", title="나 문서"),
        ],
    }

    evidences = service._to_card(row)["evidences"]

    assert [d["document_title"] for d in evidences] == ["가 문서", "나 문서"]
    assert [len(d["items"]) for d in evidences] == [1, 2]


def test_evidence_locations_sort_numbers_as_numbers_and_missing_last():
    row = {
        "id": "1",
        "card_documents": [
            _evidence_row("e1", "doc-a", None),
            _evidence_row("e2", "doc-a", "p.10"),
            _evidence_row("e3", "doc-a", "p.2"),
            _evidence_row("e4", "doc-a", "2장 설계"),
        ],
    }

    [document] = service._to_card(row)["evidences"]

    assert [item["location"] for item in document["items"]] == ["2장 설계", "p.2", "p.10", None]


def test_evidence_order_does_not_depend_on_db_row_order():
    rows = [
        _evidence_row("e1", "doc-a", "p.1", "가"),
        _evidence_row("e2", "doc-a", "p.1", "나"),
        _evidence_row("e3", "doc-b", "p.1", title="다른 문서"),
    ]

    forward = service._to_card({"id": "1", "card_documents": rows})["evidences"]
    backward = service._to_card({"id": "1", "card_documents": rows[::-1]})["evidences"]

    assert forward == backward


def test_card_without_evidences_returns_empty_list():
    assert service._to_card({"id": "1", "card_documents": []})["evidences"] == []
    assert service._to_card({"id": "1"})["evidences"] == []


def test_grouped_card_passes_response_schema(client, fake_db):
    row = {
        **CARD_ROW,
        "card_documents": [
            {**CARD_ROW["card_documents"][0], "id": EVIDENCE_ID, "location": "p.2"},
            {
                **CARD_ROW["card_documents"][0],
                "id": "1f3d7e52-5b1a-4c8e-9d11-7a2c4e6f8a90",
                "location": "p.1",
            },
        ],
    }
    fake_db(data=[row])

    response = client.get(f"/api/knowledge/cards/{CARD_ID}")

    assert response.status_code == 200
    [document] = response.json()["evidences"]
    assert document["documentId"] == DOCUMENT_ID
    assert [item["location"] for item in document["items"]] == ["p.1", "p.2"]


def test_get_card_returns_camel_case_card_with_evidences(client, fake_db):
    query = fake_db(data=[dict(CARD_ROW)])

    response = client.get(f"/api/knowledge/cards/{CARD_ID}")

    assert response.status_code == 200
    card = response.json()
    assert card["id"] == CARD_ID
    assert card["qualityScore"] == 0.92
    assert "card_documents" not in card
    assert card["evidences"][0]["documentTitle"] == "검색 시스템 설계서"
    assert query.called("select") == [((service.CARD_SELECT,), {})]
    # 목록과 같은 기준: active 카드만 조회한다
    assert query.called("eq") == [(("id", CARD_ID), {}), (("status", "active"), {})]


def test_get_card_not_found(client, fake_db):
    # 없는 카드와 active가 아닌 카드 모두 조회 결과가 비어 있다
    fake_db(data=[])

    response = client.get(f"/api/knowledge/cards/{CARD_ID}")

    assert response.status_code == 404
    assert response.json() == {"detail": "지식카드를 찾을 수 없습니다."}


def test_get_card_db_error(client, fake_db):
    fake_db(fail_with=APIError({"code": "42703", "message": "column does not exist"}))

    response = client.get(f"/api/knowledge/cards/{CARD_ID}")

    assert response.status_code == 500
    assert response.json() == {"detail": "지식카드를 조회하지 못했습니다."}


def test_get_card_invalid_id(client, fake_db):
    query = fake_db()

    response = client.get("/api/knowledge/cards/not-a-uuid")

    assert response.status_code == 422
    assert query.calls == []
