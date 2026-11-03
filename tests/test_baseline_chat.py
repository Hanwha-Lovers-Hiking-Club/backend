# 기본 RAG 챗봇 테스트.
# BM25, 검색 그래프 노드(rewrite, retrieve, rerank, expand)와 그래프 연결을 확인한다.
# 실제 OpenAI, KURE, Supabase는 호출하지 않고 가짜 함수로 바꾼다.
import asyncio

from app.domains.baseline_chat.pipeline.bm25 import BM25Index, tokenize
from app.domains.baseline_chat.pipeline.graph import build_graph
from app.domains.baseline_chat.pipeline.nodes import expand as expand_module
from app.domains.baseline_chat.pipeline.nodes import rerank as rerank_module
from app.domains.baseline_chat.pipeline.nodes import retrieve as retrieve_module
from app.domains.baseline_chat.pipeline.nodes import rewrite as rewrite_module
from app.domains.baseline_chat.pipeline.nodes.expand import expand
from app.domains.baseline_chat.pipeline.nodes.rerank import rerank
from app.domains.baseline_chat.pipeline.nodes.retrieve import reciprocal_rank_fusion, retrieve
from app.domains.baseline_chat.pipeline.nodes.rewrite import RewrittenQueries, rewrite
from app.domains.chat.schemas import ChatMessage

# ---------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------


def test_tokenize_splits_hangul_into_bigrams_and_keeps_latin_words():
    assert tokenize("결제 API타임아웃") == ["결제", "api", "타임", "임아", "아웃"]


def test_tokenize_keeps_single_hangul_syllable():
    assert tokenize("A 및 B") == ["a", "및", "b"]


def test_tokenize_ignores_punctuation():
    assert tokenize("Next.js, (Supabase)!") == ["next", "js", "supabase"]


# ---------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------


def test_matches_word_with_attached_particle():
    index = BM25Index(["결제모듈에서 장애가 발생했다", "배포 일정을 공유했다"])

    assert [i for i, _ in index.top_k("결제모듈", 5)] == [0]


def test_rare_term_scores_higher_than_common_term():
    index = BM25Index(
        [
            "서버 서버 웹소켓",
            "서버 배포",
            "서버 로그",
            "서버 모니터링",
        ]
    )

    # "웹소켓"은 한 문서에만 있어서 모든 문서에 있는 "서버"보다 점수 기여가 크다
    assert index.top_k("서버 웹소켓", 1)[0][0] == 0


def test_shorter_document_scores_higher_with_same_term_frequency():
    index = BM25Index(
        [
            "캐시 무효화 문제와 함께 다른 여러 가지 긴 설명이 이어지는 문서",
            "캐시 무효화",
        ]
    )

    assert [i for i, _ in index.top_k("캐시", 2)] == [1, 0]


def test_no_matching_token_returns_empty():
    index = BM25Index(["결제 장애", "배포 일정"])

    assert index.top_k("웹소켓", 5) == []


def test_top_k_limits_result_count():
    index = BM25Index(["결제 장애 1", "결제 장애 2", "결제 장애 3"])

    assert len(index.top_k("결제", 2)) == 2


def test_empty_corpus_returns_empty():
    assert BM25Index([]).top_k("결제", 5) == []


# ---------------------------------------------------------------
# 검색 그래프 공통 준비
# ---------------------------------------------------------------


def _chunk(chunk_id: str, document_id: str = "doc-1", chunk_index: int = 0, score=0.0) -> dict:
    return {
        "id": chunk_id,
        "document_id": document_id,
        "chunk_index": chunk_index,
        "location": None,
        "content": f"{chunk_id} 본문",
        "search_text": f"{chunk_id} 검색용",
        "score": score,
    }


class _FakeStructuredLLM:
    def __init__(self, result):
        self.result = result
        self.calls: list = []

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.result


# ---------------------------------------------------------------
# rewrite
# ---------------------------------------------------------------


def test_rewrite_puts_standalone_first_and_removes_duplicates(monkeypatch):
    llm = _FakeStructuredLLM(
        RewrittenQueries(
            standalone="결제 API 타임아웃 원인",
            alternatives=["결제 API 타임아웃 원인", " ", "결제 연동 지연 이유"],
        )
    )
    monkeypatch.setattr(rewrite_module, "get_llm", lambda: llm)

    result = asyncio.run(rewrite({"message": "그거 원인이 뭐야?", "history": []}))

    # 다른 표현 질문은 앞의 2개만 쓰고, 그중 중복과 빈 문자열은 뺀다
    assert result["queries"] == ["결제 API 타임아웃 원인"]


def test_rewrite_keeps_two_alternatives(monkeypatch):
    llm = _FakeStructuredLLM(
        RewrittenQueries(standalone="질문", alternatives=["표현1", "표현2", "표현3"])
    )
    monkeypatch.setattr(rewrite_module, "get_llm", lambda: llm)

    result = asyncio.run(rewrite({"message": "질문", "history": []}))

    assert result["queries"] == ["질문", "표현1", "표현2"]


def test_rewrite_sends_recent_history(monkeypatch):
    llm = _FakeStructuredLLM(RewrittenQueries(standalone="질문", alternatives=[]))
    monkeypatch.setattr(rewrite_module, "get_llm", lambda: llm)
    history = [ChatMessage(role="user", content=f"이전 질문 {i}") for i in range(10)]

    asyncio.run(rewrite({"message": "그거 원인은?", "history": history}))

    prompt = llm.calls[0][-1].content
    assert "이전 질문 9" in prompt
    assert "이전 질문 3" not in prompt  # 최근 6개만 보낸다
    assert prompt.index("<history>") < prompt.index("<question>")


# ---------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------


def test_rrf_ranks_item_found_by_multiple_lists_first():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])

    assert [chunk_id for chunk_id, _ in fused] == ["b", "a", "c"]


def _patch_retrieve(monkeypatch, chunks: list[dict], vector_ids: list[str]) -> None:
    monkeypatch.setattr(retrieve_module.repository, "get_all_chunks", lambda: chunks)
    monkeypatch.setattr(
        retrieve_module.repository,
        "match_chunks",
        lambda embedding, count: [{"id": chunk_id} for chunk_id in vector_ids],
    )
    monkeypatch.setattr(retrieve_module, "embed_queries", lambda texts: [[0.0] for _ in texts])


def test_retrieve_fuses_vector_and_bm25_results(monkeypatch):
    chunks = [
        {**_chunk("a"), "search_text": "배포 일정 공유"},
        {**_chunk("b"), "search_text": "결제 장애 원인 분석"},
        {**_chunk("c"), "search_text": "회의록"},
    ]
    # 벡터 검색은 c, b 순서로 찾고, BM25는 "결제 장애"로 b만 찾는다
    _patch_retrieve(monkeypatch, chunks, ["c", "b"])

    result = asyncio.run(retrieve({"queries": ["결제 장애"]}))

    assert [c["id"] for c in result["candidates"]] == ["b", "c"]
    assert result["candidates"][0]["content"] == "b 본문"


def test_retrieve_skips_vector_result_missing_from_chunks(monkeypatch):
    _patch_retrieve(monkeypatch, [_chunk("a")], ["deleted", "a"])

    result = asyncio.run(retrieve({"queries": ["질문"]}))

    assert [c["id"] for c in result["candidates"]] == ["a"]


def test_retrieve_with_no_chunks_returns_empty(monkeypatch):
    _patch_retrieve(monkeypatch, [], [])

    assert asyncio.run(retrieve({"queries": ["질문"]})) == {"candidates": []}


# ---------------------------------------------------------------
# rerank
# ---------------------------------------------------------------


def test_rerank_without_api_key_uses_rrf_top_five(monkeypatch):
    monkeypatch.setattr(rerank_module.settings, "RERANK_API_KEY", None)
    candidates = [_chunk(str(i), score=1.0 - i / 10) for i in range(8)]

    result = asyncio.run(rerank({"queries": ["질문"], "candidates": candidates}))

    assert [c["id"] for c in result["reranked"]] == ["0", "1", "2", "3", "4"]


def test_rerank_with_api_key_follows_api_order(monkeypatch):
    monkeypatch.setattr(rerank_module.settings, "RERANK_API_KEY", "test-key")
    sent = {}

    async def fake_api(query, documents, top_n):
        sent.update(query=query, documents=documents, top_n=top_n)
        return [(2, 0.9), (0, 0.4)]

    monkeypatch.setattr(rerank_module, "call_rerank_api", fake_api)
    candidates = [_chunk("a"), _chunk("b"), _chunk("c")]

    result = asyncio.run(rerank({"queries": ["독립 질문", "다른 표현"], "candidates": candidates}))

    assert [(c["id"], c["score"]) for c in result["reranked"]] == [("c", 0.9), ("a", 0.4)]
    assert sent["query"] == "독립 질문"
    assert sent["documents"] == ["a 검색용", "b 검색용", "c 검색용"]


def test_rerank_with_no_candidates_returns_empty():
    assert asyncio.run(rerank({"queries": ["질문"], "candidates": []})) == {"reranked": []}


# ---------------------------------------------------------------
# expand
# ---------------------------------------------------------------


def test_expand_adds_previous_and_next_chunks(monkeypatch):
    stored = {("doc-1", 0): "첫 청크", ("doc-1", 1): "둘째 청크", ("doc-1", 2): "셋째 청크"}
    requested = []

    def fake_get(document_id, indexes):
        requested.append((document_id, indexes))
        return [
            {"chunk_index": i, "content": stored[(document_id, i)]}
            for i in indexes
            if (document_id, i) in stored
        ]

    monkeypatch.setattr(expand_module.repository, "get_chunks_by_index", fake_get)
    first = {**_chunk("x", chunk_index=0), "content": "첫 청크"}
    middle = {**_chunk("y", chunk_index=1), "content": "둘째 청크"}

    result = asyncio.run(expand({"reranked": [middle, first]}))

    contexts = [c["context"] for c in result["contexts"]]
    assert contexts == ["첫 청크\n둘째 청크\n셋째 청크", "첫 청크\n둘째 청크"]
    # 같은 문서는 한 번만 조회하고, 음수 순번은 요청하지 않는다
    assert requested == [("doc-1", [0, 1, 2])]


# ---------------------------------------------------------------
# 그래프 연결
# ---------------------------------------------------------------


def test_graph_runs_rewrite_to_expand(monkeypatch):
    llm = _FakeStructuredLLM(RewrittenQueries(standalone="결제 장애", alternatives=[]))
    monkeypatch.setattr(rewrite_module, "get_llm", lambda: llm)
    _patch_retrieve(monkeypatch, [{**_chunk("a"), "search_text": "결제 장애 원인"}], ["a"])
    monkeypatch.setattr(rerank_module.settings, "RERANK_API_KEY", None)
    monkeypatch.setattr(expand_module.repository, "get_chunks_by_index", lambda d, i: [])

    result = asyncio.run(build_graph().ainvoke({"message": "결제 장애 원인?", "history": []}))

    assert result["queries"] == ["결제 장애"]
    assert [c["id"] for c in result["contexts"]] == ["a"]
    assert result["contexts"][0]["context"] == "a 본문"
