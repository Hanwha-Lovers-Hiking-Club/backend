# 챗봇 검색 단계 테스트. 임베딩 호출, DB RPC, 카드 조회를 가짜로 바꿔 후보를 합치고 추리는
# 로직만 본다.
import pytest

from app.domains.chat import retriever
from app.domains.chat.models import Intent

pytestmark = pytest.mark.anyio


def _intent(kind) -> Intent:
    return Intent(
        kind=kind, search_query="질문", needs_clarification=False, clarifying_question=None
    )


def _id(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


@pytest.fixture
def fake_db(monkeypatch, make_card):
    # 임베딩 종류별 RPC 결과를 테스트가 채우고, 카드 조회는 요청한 id마다 카드를 만들어 준다
    state = {"rpc": {}, "kinds": [], "embedded": [], "fetched": [], "missing": []}

    def embed(text):
        state["embedded"].append(text)
        return [0.0]

    def match(embedding, kind):
        state["kinds"].append(kind)
        return state["rpc"].get(kind, [])

    def fetch(card_ids):
        state["fetched"] = card_ids
        return {
            card_id: make_card(title=f"카드 {card_id}").model_dump(mode="json")
            for card_id in card_ids
            if card_id not in state["missing"]
        }

    monkeypatch.setattr(retriever, "_embed_query", embed)
    monkeypatch.setattr(retriever, "_match", match)
    monkeypatch.setattr(retriever, "get_active_cards_by_ids", fetch)
    return state


def test_searches_intent_embedding_and_content_embedding(fake_db):
    retriever._search("질문", "situation")

    assert fake_db["kinds"] == ["situation", "content"]
    assert fake_db["embedded"] == ["질문"]


def test_content_intent_searches_only_once(fake_db):
    retriever._search("질문", "content")

    assert fake_db["kinds"] == ["content"]


def test_merges_by_best_similarity_and_sorts(fake_db):
    fake_db["rpc"] = {
        "lesson": [{"id": _id(1), "similarity": 0.5}, {"id": _id(2), "similarity": 0.7}],
        "content": [{"id": _id(1), "similarity": 0.9}, {"id": _id(3), "similarity": 0.6}],
    }

    candidates = retriever._search("질문", "lesson")

    assert [(c.card.title, c.similarity) for c in candidates] == [
        (f"카드 {_id(1)}", 0.9),
        (f"카드 {_id(2)}", 0.7),
        (f"카드 {_id(3)}", 0.6),
    ]


def test_caps_candidates(fake_db):
    fake_db["rpc"] = {"content": [{"id": _id(n), "similarity": 1 - n / 100} for n in range(1, 9)]}

    candidates = retriever._search("질문", "content")

    assert len(candidates) == retriever.MAX_CANDIDATES
    assert fake_db["fetched"] == [_id(n) for n in range(1, retriever.MAX_CANDIDATES + 1)]


def test_skips_cards_that_disappeared(fake_db):
    fake_db["rpc"] = {
        "content": [{"id": _id(1), "similarity": 0.9}, {"id": _id(2), "similarity": 0.8}]
    }
    fake_db["missing"] = [_id(1)]

    candidates = retriever._search("질문", "content")

    assert [c.card.title for c in candidates] == [f"카드 {_id(2)}"]


def test_no_match_returns_empty(fake_db):
    assert retriever._search("질문", "content") == []


@pytest.mark.parametrize(
    ("kind", "embedding_kind"),
    [
        ("similar_project", "content"),
        ("similar_issue", "situation"),
        ("solution", "solution"),
        ("similar_lesson", "lesson"),
        ("tech_stack", "situation"),
        ("general", "content"),
    ],
)
async def test_intent_selects_embedding_kind(monkeypatch, kind, embedding_kind):
    received = {}

    def fake_search(query, selected):
        received["args"] = (query, selected)
        return []

    monkeypatch.setattr(retriever, "_search", fake_search)

    await retriever.search_cards(_intent(kind))

    assert received["args"] == ("질문", embedding_kind)
