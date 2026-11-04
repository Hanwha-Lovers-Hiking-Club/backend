# 챗봇 검색 단계 테스트. 임베딩 호출, DB RPC, 근거/카드 조회, 원문 검색을 가짜로 바꿔 후보를
# 합치고 연결하고 추리는 로직만 본다.
import pytest

from app.domains.chat import retriever
from app.domains.chat.models import ChunkCandidate, Intent

pytestmark = pytest.mark.anyio


def _intent(kind) -> Intent:
    return Intent(
        is_dev_domain=True,
        kind=kind,
        is_follow_up=False,
        search_query="질문",
        key_terms=["용어"],
        needs_clarification=False,
        clarifying_question=None,
    )


def _id(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def _chunk(
    n: int, content: str, document: str = "doc-1", similarity: float | None = 0.3
) -> ChunkCandidate:
    return ChunkCandidate(
        id=f"chunk-{n}",
        document_id=document,
        document_title="문서",
        chunk_index=n,
        location=None,
        content=content,
        score=0.01,
        similarity=similarity,
    )


@pytest.fixture
def fake_db(monkeypatch, make_card):
    # 임베딩 종류별 RPC 결과와 근거(card_documents)를 테스트가 채우고, 카드 조회는 요청한 id마다
    # 카드를 만들어 준다(missing에 넣은 id는 없거나 active가 아닌 카드)
    state = {"rpc": {}, "kinds": [], "evidences": [], "fetched": [], "missing": []}

    def match(embedding, kind):
        state["kinds"].append(kind)
        return state["rpc"].get(kind, [])

    def evidences(document_ids):
        state["evidence_docs"] = document_ids
        return [e for e in state["evidences"] if e["document_id"] in document_ids]

    def fetch(card_ids):
        state["fetched"] = card_ids
        return {
            card_id: make_card(title=f"카드 {card_id}").model_dump(mode="json")
            for card_id in card_ids
            if card_id not in state["missing"]
        }

    monkeypatch.setattr(retriever, "_match", match)
    monkeypatch.setattr(retriever, "_evidences", evidences)
    monkeypatch.setattr(retriever, "get_active_cards_by_ids", fetch)
    return state


def _rank(fake_db, kind="content", chunks=()):
    return retriever._rank_cards(retriever._vector_similarities([0.0], kind), list(chunks))


def test_searches_intent_embedding_and_content_embedding(fake_db):
    retriever._vector_similarities([0.0], "situation")

    assert fake_db["kinds"] == ["situation", "content"]


def test_content_intent_searches_only_once(fake_db):
    retriever._vector_similarities([0.0], "content")

    assert fake_db["kinds"] == ["content"]


def test_merges_by_best_similarity_and_sorts(fake_db):
    fake_db["rpc"] = {
        "lesson": [{"id": _id(1), "similarity": 0.5}, {"id": _id(2), "similarity": 0.7}],
        "content": [{"id": _id(1), "similarity": 0.9}, {"id": _id(3), "similarity": 0.6}],
    }

    candidates = _rank(fake_db, "lesson")

    assert [(c.card.title, c.similarity) for c in candidates] == [
        (f"카드 {_id(1)}", 0.9),
        (f"카드 {_id(2)}", 0.7),
        (f"카드 {_id(3)}", 0.6),
    ]


def test_caps_candidates(fake_db):
    fake_db["rpc"] = {"content": [{"id": _id(n), "similarity": 1 - n / 100} for n in range(1, 9)]}

    candidates = _rank(fake_db)

    assert [c.card.title for c in candidates] == [
        f"카드 {_id(n)}" for n in range(1, retriever.MAX_CANDIDATES + 1)
    ]


def test_skips_cards_that_are_gone_or_not_active_before_capping(fake_db):
    fake_db["rpc"] = {"content": [{"id": _id(n), "similarity": 1 - n / 100} for n in range(1, 9)]}
    fake_db["missing"] = [_id(1), _id(2)]

    candidates = _rank(fake_db)

    # 빠진 카드가 자리를 차지하지 않아서 다음 순위 카드로 MAX_CANDIDATES를 채운다
    assert [c.card.title for c in candidates] == [
        f"카드 {_id(n)}" for n in range(3, retriever.MAX_CANDIDATES + 3)
    ]


def test_no_match_returns_empty(fake_db):
    assert _rank(fake_db) == []
    assert fake_db["fetched"] == []


@pytest.mark.parametrize(
    ("content", "excerpt", "expected"),
    [
        ("결제 상태를\n  APPROVED로 덮어썼다", "결제 상태를 APPROVED로 덮어썼다", True),
        # 공백을 뺀 앞 30자가 같으면 끝이 달라도 같은 위치로 본다
        (
            "취소 웹훅 뒤에 재전송된 승인 웹훅이 마지막 수신 상태로 APPROVED를 덮어썼다. 표 끝",
            "취소 웹훅 뒤에 재전송된 승인 웹훅이 마지막 수신 상태로 APPROVED를 덮어썼다 (변환)",
            True,
        ),
        (
            "취소 웹훅 뒤에 재전송된 승인 웹훅이",
            "취소 웹훅 뒤에 재전송된 다른 웹훅이 마지막 수신 상태로 덮어썼다",
            False,
        ),
        ("짧은 발췌문", "짧은 다른 문장", False),
        ("내용", "   ", False),
    ],
)
def test_contains_excerpt_ignores_whitespace_and_allows_long_prefix(content, excerpt, expected):
    assert retriever._contains_excerpt(content, excerpt) is expected


def test_links_card_only_when_its_excerpt_is_in_the_chunk(fake_db):
    fake_db["evidences"] = [
        {"card_id": _id(1), "document_id": "doc-1", "excerpt": "웹훅 순서 역전"},
        {"card_id": _id(2), "document_id": "doc-1", "excerpt": "다른 위치의 문장"},
        # 같은 문장이라도 다른 문서의 근거면 연결하지 않는다
        {"card_id": _id(3), "document_id": "doc-2", "excerpt": "웹훅 순서 역전"},
        {"card_id": _id(4), "document_id": "doc-1", "excerpt": None},
    ]
    chunks = [_chunk(0, "취소 뒤 웹훅 순서 역전이 생겼다"), _chunk(1, "무관", document="doc-2")]

    assert retriever.link_cards_to_chunks(chunks) == {_id(1): ["chunk-0"]}
    assert fake_db["evidence_docs"] == ["doc-1", "doc-2"]


def test_linked_card_moves_ahead_and_records_its_chunks(fake_db):
    fake_db["rpc"] = {
        "content": [{"id": _id(1), "similarity": 0.5}, {"id": _id(2), "similarity": 0.45}]
    }
    fake_db["evidences"] = [{"card_id": _id(2), "document_id": "doc-1", "excerpt": "근거 문장"}]

    candidates = _rank(fake_db, chunks=[_chunk(0, "여기 근거 문장이 있다")])

    assert [c.card.title for c in candidates] == [f"카드 {_id(2)}", f"카드 {_id(1)}"]
    assert candidates[0].linked_chunk_ids == ["chunk-0"]
    assert candidates[1].linked_chunk_ids == []


def test_card_found_only_through_source_has_no_similarity(fake_db):
    fake_db["evidences"] = [{"card_id": _id(7), "document_id": "doc-1", "excerpt": "근거 문장"}]

    [candidate] = _rank(fake_db, chunks=[_chunk(0, "근거 문장")])

    assert candidate.similarity is None
    assert candidate.linked_chunk_ids == ["chunk-0"]


def test_keyword_only_chunks_do_not_pull_in_cards(fake_db):
    fake_db["rpc"] = {"content": [{"id": _id(1), "similarity": 0.3}]}
    fake_db["evidences"] = [
        {"card_id": _id(1), "document_id": "doc-1", "excerpt": "근거 문장"},
        {"card_id": _id(2), "document_id": "doc-1", "excerpt": "근거 문장"},
    ]

    candidates = _rank(fake_db, chunks=[_chunk(0, "근거 문장", similarity=None)])

    assert [c.card.title for c in candidates] == [f"카드 {_id(1)}"]
    assert candidates[0].linked_chunk_ids == []


def test_no_chunks_skips_evidence_lookup(monkeypatch):
    def forbidden(*args):
        raise AssertionError("원문 후보가 없으면 근거를 조회하면 안 된다")

    monkeypatch.setattr(retriever.supabase, "table", forbidden)

    assert retriever.link_cards_to_chunks([]) == {}


async def test_search_embeds_once_and_shares_embedding(monkeypatch, fake_db):
    calls = {"embedded": [], "chunk_args": None}

    def embed(text):
        calls["embedded"].append(text)
        return [0.3]

    async def chunks(query, embedding, terms):
        calls["chunk_args"] = (query, embedding, terms)
        return [_chunk(0, "근거 문장")]

    def match(embedding, kind):
        calls.setdefault("match_embeddings", []).append(embedding)
        return []

    monkeypatch.setattr(retriever, "embed_query", embed)
    monkeypatch.setattr(retriever, "search_chunks", chunks)
    monkeypatch.setattr(retriever, "_match", match)
    fake_db["evidences"] = [{"card_id": _id(1), "document_id": "doc-1", "excerpt": "근거 문장"}]

    result = await retriever.search(_intent("solution"))

    assert calls["embedded"] == ["질문"]
    # 질문 분석이 뽑은 핵심 용어도 원문 키워드 검색에 넘긴다
    assert calls["chunk_args"] == ("질문", [0.3], ["용어"])
    assert calls["match_embeddings"] == [[0.3], [0.3]]
    assert [c.id for c in result.chunks] == ["chunk-0"]
    assert [c.linked_chunk_ids for c in result.cards] == [["chunk-0"]]


@pytest.mark.parametrize(
    ("kind", "embedding_kind"),
    [
        ("similar_project", "content"),
        ("similar_issue", "situation"),
        ("solution", "solution"),
        ("similar_lesson", "lesson"),
        ("tech_stack", "situation"),
        ("concept", "content"),
        ("general", "content"),
    ],
)
async def test_intent_selects_embedding_kind(monkeypatch, kind, embedding_kind):
    received = {}

    def fake_similarities(embedding, selected):
        received["kind"] = selected
        return {}

    async def no_chunks(query, embedding, terms):
        return []

    monkeypatch.setattr(retriever, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(retriever, "_vector_similarities", fake_similarities)
    monkeypatch.setattr(retriever, "search_chunks", no_chunks)
    monkeypatch.setattr(retriever, "_rank_cards", lambda similarities, chunks: [])

    await retriever.search_cards(_intent(kind))

    assert received["kind"] == embedding_kind
