# 기본 RAG 챗봇 테스트.
# BM25, 검색 그래프 노드(rewrite, retrieve, rerank, expand), 그래프 연결, 답변 생성(generate),
# 챗봇 엔드포인트를 확인한다.
# 실제 OpenAI, KURE, Supabase는 호출하지 않고 가짜 함수로 바꾼다.
import asyncio
import json
from uuid import uuid4

from langchain_core.messages import AIMessageChunk

from app.domains.baseline_chat import service as chat_service
from app.domains.baseline_chat.pipeline.bm25 import BM25Index, tokenize
from app.domains.baseline_chat.pipeline.graph import build_graph
from app.domains.baseline_chat.pipeline.nodes import expand as expand_module
from app.domains.baseline_chat.pipeline.nodes import generate as generate_module
from app.domains.baseline_chat.pipeline.nodes import rerank as rerank_module
from app.domains.baseline_chat.pipeline.nodes import retrieve as retrieve_module
from app.domains.baseline_chat.pipeline.nodes import rewrite as rewrite_module
from app.domains.baseline_chat.pipeline.nodes.expand import expand
from app.domains.baseline_chat.pipeline.nodes.generate import NO_CONTEXT_ANSWER, generate
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


# ---------------------------------------------------------------
# generate
# ---------------------------------------------------------------


class _FakeStreamingLLM:
    def __init__(self, tokens: list[str], error: Exception | None = None):
        self.tokens = tokens
        self.error = error
        self.calls: list = []

    async def astream(self, messages):
        self.calls.append(messages)
        for token in self.tokens:
            yield AIMessageChunk(content=token)
        if self.error:
            raise self.error


def _context(chunk_id: str, document_id: str, location: str | None, context: str) -> dict:
    return {**_chunk(chunk_id, document_id), "location": location, "context": context}


async def _collect(generator) -> list[str]:
    return [token async for token in generator]


def test_generate_streams_llm_tokens(monkeypatch):
    llm = _FakeStreamingLLM(["결제 장애는 ", "", "타임아웃 때문입니다 [1]."])
    monkeypatch.setattr(generate_module, "get_llm", lambda: llm)
    contexts = [_context("a", "doc-1", "장애 대응", "타임아웃 설명")]

    tokens = asyncio.run(_collect(generate("원인은?", [], contexts, {"doc-1": "결제 회고"})))

    # 빈 토큰은 보내지 않는다
    assert tokens == ["결제 장애는 ", "타임아웃 때문입니다 [1]."]


def test_generate_numbers_contexts_and_keeps_history(monkeypatch):
    llm = _FakeStreamingLLM(["답"])
    monkeypatch.setattr(generate_module, "get_llm", lambda: llm)
    contexts = [
        _context("a", "doc-1", "장애 대응", "첫 컨텍스트"),
        _context("b", "doc-2", None, "둘째 컨텍스트"),
    ]
    history = [
        ChatMessage(role="user", content="이전 질문"),
        ChatMessage(role="assistant", content="이전 답변"),
    ]

    asyncio.run(_collect(generate("원인은?", history, contexts, {"doc-1": "결제 회고"})))

    messages = llm.calls[0]
    assert [m.content for m in messages[1:3]] == ["이전 질문", "이전 답변"]
    prompt = messages[-1].content
    assert "[1] 결제 회고 · 장애 대응\n첫 컨텍스트" in prompt
    # 제목을 못 찾은 문서와 위치가 없는 청크
    assert "[2] 제목 없음\n둘째 컨텍스트" in prompt
    assert prompt.index("<documents>") < prompt.index("<question>")


def test_generate_without_contexts_does_not_call_llm(monkeypatch):
    llm = _FakeStreamingLLM(["호출되면 안 됨"])
    monkeypatch.setattr(generate_module, "get_llm", lambda: llm)

    tokens = asyncio.run(_collect(generate("원인은?", [], [], {})))

    assert tokens == [NO_CONTEXT_ANSWER]
    assert llm.calls == []


# ---------------------------------------------------------------
# 챗봇 엔드포인트 (POST /api/baseline/chat/stream)
# ---------------------------------------------------------------

CHAT_URL = "/api/baseline/chat/stream"


def _events(text: str) -> list[tuple[str, dict]]:
    # SSE 본문을 (이벤트 이름, data JSON) 목록으로 바꾼다 (tests/test_chat_stream.py와 같은 방식)
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


class _FakeGraph:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.result = result
        self.error = error

    async def ainvoke(self, state):
        if self.error:
            raise self.error
        return self.result


def _patch_chat(monkeypatch, graph: _FakeGraph, llm: _FakeStreamingLLM) -> None:
    monkeypatch.setattr(chat_service, "get_graph", lambda: graph)
    monkeypatch.setattr(
        chat_service.repository,
        "get_document_titles",
        lambda ids: {document_id: "결제 회고" for document_id in ids},
    )
    monkeypatch.setattr(generate_module, "get_llm", lambda: llm)


def test_chat_streams_tokens_then_references_then_completed(client, monkeypatch):
    chunk_id, document_id = str(uuid4()), str(uuid4())
    context = {
        **_context(chunk_id, document_id, "장애 대응", "앞뒤를 붙인 컨텍스트"),
        "score": 0.8,
    }
    _patch_chat(
        monkeypatch, _FakeGraph({"contexts": [context]}), _FakeStreamingLLM(["답변 ", "[1]"])
    )

    response = client.post(CHAT_URL, json={"message": "결제 장애 원인?"})

    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "token", "references", "completed"]
    assert "".join(data["content"] for name, data in events if name == "token") == "답변 [1]"
    assert events[2][1] == {
        "items": [
            {
                "number": 1,
                "chunkId": chunk_id,
                "documentId": document_id,
                "documentTitle": "결제 회고",
                "location": "장애 대응",
                # 근거 발췌문은 앞뒤 청크를 붙이기 전의 본문이다
                "excerpt": f"{chunk_id} 본문",
                "score": 0.8,
            }
        ]
    }


def test_chat_without_contexts_answers_not_found(client, monkeypatch):
    _patch_chat(monkeypatch, _FakeGraph({"contexts": []}), _FakeStreamingLLM([]))

    events = _events(client.post(CHAT_URL, json={"message": "질문"}).text)

    assert events == [
        ("token", {"content": NO_CONTEXT_ANSWER}),
        ("references", {"items": []}),
        ("completed", {}),
    ]


def test_chat_graph_failure_sends_error_event(client, monkeypatch):
    _patch_chat(monkeypatch, _FakeGraph(error=RuntimeError("db down")), _FakeStreamingLLM([]))

    events = _events(client.post(CHAT_URL, json={"message": "질문"}).text)

    assert events == [("error", {"message": "답변을 생성하지 못했습니다."})]


def test_chat_llm_failure_mid_stream_ends_with_error(client, monkeypatch):
    context = {**_context(str(uuid4()), str(uuid4()), None, "컨텍스트"), "score": 0.1}
    llm = _FakeStreamingLLM(["답변 일부"], error=RuntimeError("openai down"))
    _patch_chat(monkeypatch, _FakeGraph({"contexts": [context]}), llm)

    events = _events(client.post(CHAT_URL, json={"message": "질문"}).text)

    assert [name for name, _ in events] == ["token", "error"]


def test_chat_route_is_registered(client):
    assert CHAT_URL in client.get("/openapi.json").json()["paths"]
