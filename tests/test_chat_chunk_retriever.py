# 원문 검색 단계 테스트. 벡터 RPC와 청크/문서 조회를 가짜로 바꿔 합치고 추리는 로직만 본다.
import pytest

from app.domains.chat import chunk_retriever

pytestmark = pytest.mark.anyio


def _chunk(n: int, content: str, document: str = "doc-1", index: int | None = None) -> dict:
    return {
        "id": f"chunk-{n}",
        "document_id": document,
        "chunk_index": n if index is None else index,
        "location": f"{n}장",
        "content": content,
    }


@pytest.fixture
def fake_db(monkeypatch):
    # 테스트가 청크 목록과 벡터 결과를 채운다. 전체 청크를 몇 번 읽었는지도 센다
    state = {"chunks": [], "vector": [], "loads": 0, "rpc_args": None}

    def load_chunks():
        state["loads"] += 1
        return list(state["chunks"])

    def match(embedding):
        state["rpc_args"] = embedding
        return state["vector"]

    monkeypatch.setattr(chunk_retriever, "_corpus", None)
    monkeypatch.setattr(chunk_retriever, "_load_chunks", load_chunks)
    monkeypatch.setattr(chunk_retriever, "_load_titles", lambda: {"doc-1": "PAY-310 대응기록"})
    monkeypatch.setattr(chunk_retriever, "_match", match)
    return state


def test_keyword_query_adds_every_spelling_of_found_technologies():
    query = chunk_retriever.keyword_query("WebSocket으로 오픈 신호를 보낸 이유")

    assert query.startswith("WebSocket으로 오픈 신호를 보낸 이유")
    assert "웹소켓" in query


def test_keyword_query_adds_key_terms_and_their_technology_aliases():
    query = chunk_retriever.keyword_query(
        "예약 완료 표시 문제", ["optimistic update", "낙관적 업데이트", "k8s"]
    )

    assert "optimistic update" in query and "낙관적 업데이트" in query
    # 용어에 나온 기술도 사전 별칭으로 넓힌다
    assert "쿠버네티스" in query


def test_keyword_query_without_technologies_is_unchanged():
    assert chunk_retriever.keyword_query("정산 배치 중복 지급") == "정산 배치 중복 지급"


def test_rrf_rewards_items_found_by_both_lists():
    fused = chunk_retriever.reciprocal_rank_fusion([["a", "b"], ["b", "c"]])

    assert [item for item, _ in fused] == ["b", "a", "c"]


def test_merges_vector_and_keyword_results(fake_db):
    fake_db["chunks"] = [
        _chunk(0, "대기실 서버 시각"),
        _chunk(1, "웹소켓으로 오픈 신호를 보낸다"),
        _chunk(2, "정산 배치"),
    ]
    fake_db["vector"] = [{"id": "chunk-0", "similarity": 0.5}]

    candidates = chunk_retriever._search_chunks("WebSocket 오픈 신호", [0.1])

    assert [c.id for c in candidates] == ["chunk-0", "chunk-1"]
    assert candidates[0].similarity == 0.5
    # 키워드 검색으로만 들어온 청크는 벡터 유사도가 없다
    assert candidates[1].similarity is None
    assert candidates[1].document_title == "PAY-310 대응기록"
    assert fake_db["rpc_args"] == [0.1]


def test_document_title_is_searchable_by_keyword(fake_db):
    fake_db["chunks"] = [_chunk(0, "웹훅이 늦게 도착했다")]

    candidates = chunk_retriever._search_chunks("PAY-310", [0.1])

    assert [c.id for c in candidates] == ["chunk-0"]


def test_attaches_neighbor_chunks_from_same_document(fake_db):
    fake_db["chunks"] = [
        _chunk(0, "앞 내용"),
        _chunk(1, "가운데 결제"),
        _chunk(2, "뒤 내용"),
        _chunk(3, "다른 문서", document="doc-2", index=2),
    ]
    fake_db["vector"] = [{"id": "chunk-1", "similarity": 0.4}]

    [candidate] = chunk_retriever._search_chunks("질문", [0.1])

    assert (candidate.before, candidate.after) == ("앞 내용", "뒤 내용")


def test_first_and_last_chunk_have_no_outside_neighbors(fake_db):
    fake_db["chunks"] = [_chunk(0, "처음 결제")]
    fake_db["vector"] = [{"id": "chunk-0", "similarity": 0.4}]

    [candidate] = chunk_retriever._search_chunks("질문", [0.1])

    assert candidate.before is None and candidate.after is None


def test_caps_candidates(fake_db):
    fake_db["chunks"] = [_chunk(n, f"결제 {n}") for n in range(12)]
    fake_db["vector"] = [{"id": f"chunk-{n}", "similarity": 0.5} for n in range(12)]

    candidates = chunk_retriever._search_chunks("결제", [0.1])

    assert len(candidates) == chunk_retriever.MAX_CHUNK_CANDIDATES


def test_reuses_loaded_chunks_until_unknown_chunk_appears(fake_db):
    fake_db["chunks"] = [_chunk(0, "결제")]
    fake_db["vector"] = [{"id": "chunk-0", "similarity": 0.4}]
    chunk_retriever._search_chunks("결제", [0.1])
    chunk_retriever._search_chunks("결제", [0.1])
    assert fake_db["loads"] == 1

    # 방금 업로드된 문서의 청크를 벡터 검색이 찾으면 다시 읽는다
    fake_db["chunks"].append(_chunk(1, "새 문서"))
    fake_db["vector"] = [{"id": "chunk-1", "similarity": 0.4}]
    candidates = chunk_retriever._search_chunks("새 문서", [0.1])

    assert fake_db["loads"] == 2
    assert candidates[0].id == "chunk-1"


def test_drops_vector_results_missing_even_after_reload(fake_db):
    fake_db["chunks"] = [_chunk(0, "결제")]
    fake_db["vector"] = [{"id": "deleted", "similarity": 0.9}, {"id": "chunk-0", "similarity": 0.4}]

    candidates = chunk_retriever._search_chunks("질문", [0.1])

    assert [c.id for c in candidates] == ["chunk-0"]


def test_no_chunks_returns_empty(fake_db):
    assert chunk_retriever._search_chunks("질문", [0.1]) == []


async def test_search_chunks_runs_in_thread(fake_db):
    fake_db["chunks"] = [_chunk(0, "결제")]

    candidates = await chunk_retriever.search_chunks("결제", [0.1])

    assert [c.id for c in candidates] == ["chunk-0"]
