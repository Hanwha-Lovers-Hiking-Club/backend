# 기본 RAG 문서 인덱싱 테스트 (청크 분할, 문맥 보강, 임베딩, 저장, 그래프 연결, 엔드포인트).
# 실제 OpenAI, KURE, Supabase는 호출하지 않고 가짜 함수로 바꾼다.
import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.domains.baseline_documents import service as service_module
from app.domains.baseline_documents.pipeline.graph import build_graph
from app.domains.baseline_documents.pipeline.nodes import contextualize as contextualize_module
from app.domains.baseline_documents.pipeline.nodes import embed as embed_module
from app.domains.baseline_documents.pipeline.nodes import parse as parse_module
from app.domains.baseline_documents.pipeline.nodes import save as save_module
from app.domains.baseline_documents.pipeline.nodes.chunk import (
    CHUNK_SIZE_TOKENS,
    chunk,
    split_document,
)
from app.domains.baseline_documents.pipeline.nodes.contextualize import (
    build_header,
    contextualize,
)
from app.domains.baseline_documents.pipeline.nodes.embed import embed
from app.domains.baseline_documents.pipeline.nodes.save import save
from app.domains.documents.pipeline.nodes.chunk import _tokenizer


def _document(text: str, title: str = "테스트 문서") -> dict:
    return {"document_id": uuid4(), "title": title, "text": text}


def _long_text(sentences: int) -> str:
    sentence = "번째 문장은 청크 분할을 확인하기 위한 내용입니다."
    return " ".join(f"{i}{sentence}" for i in range(sentences))


# ---------------------------------------------------------------
# chunk
# ---------------------------------------------------------------


def test_chunks_do_not_cross_sections():
    document = _document(
        "# 1장\n첫 번째 섹션 본문\n## 배경\n하위 섹션 본문\n# 2장\n두 번째 섹션 본문\n"
    )

    chunks = split_document(document)

    assert [c["location"] for c in chunks] == ["1장", "1장 · 배경", "2장"]
    assert "두 번째" not in chunks[0]["content"]
    assert "첫 번째" not in chunks[2]["content"]


def test_long_section_is_split_within_token_limit():
    chunks = split_document(_document(_long_text(300)))

    assert len(chunks) > 1
    assert all(len(_tokenizer.encode(c["content"])) <= CHUNK_SIZE_TOKENS for c in chunks)


def test_consecutive_chunks_overlap():
    chunks = split_document(_document(_long_text(300)))

    # 앞 청크의 마지막 문장 일부가 다음 청크 앞부분에 다시 나와야 한다
    tail = chunks[0]["content"].split()[-3:]
    assert " ".join(tail) in chunks[1]["content"]


def test_chunk_index_starts_from_zero_per_document():
    first = _document(_long_text(300))
    second = _document("짧은 문서")

    result = chunk({"parsed_documents": [first, second]})["chunks"]

    first_indexes = [c["chunk_index"] for c in result if c["document_id"] == first["document_id"]]
    second_indexes = [c["chunk_index"] for c in result if c["document_id"] == second["document_id"]]
    assert first_indexes == list(range(len(first_indexes)))
    assert second_indexes == [0]


# ---------------------------------------------------------------
# contextualize
# ---------------------------------------------------------------


class _FakeLLM:
    def __init__(self, error: Exception | None = None):
        self.calls: list = []
        self.error = error

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return AIMessage(content="  이 청크는 결제 모듈 장애 대응 과정의 원인 분석 부분이다.  ")


def _run_contextualize(monkeypatch, llm: _FakeLLM, document: dict) -> dict:
    monkeypatch.setattr(contextualize_module, "get_llm", lambda: llm)
    chunks = split_document(document)
    return asyncio.run(contextualize({"parsed_documents": [document], "chunks": chunks}))


def test_search_text_has_header_context_and_content(monkeypatch):
    document = _document("# 장애 대응\n결제 API 타임아웃이 발생했다.", title="결제 회고")

    result = _run_contextualize(monkeypatch, _FakeLLM(), document)

    [item] = result["contextualized_chunks"]
    assert item["search_text"] == (
        "결제 회고 · 장애 대응\n"
        "이 청크는 결제 모듈 장애 대응 과정의 원인 분석 부분이다.\n\n"
        f"{item['content']}"
    )


def test_header_without_location_uses_title_only():
    assert build_header("결제 회고", "") == "결제 회고"


def test_document_comes_before_chunk_in_prompt(monkeypatch):
    llm = _FakeLLM()
    document = _document("# 장애 대응\n결제 API 타임아웃이 발생했다.")

    _run_contextualize(monkeypatch, llm, document)

    user_message = llm.calls[0][-1].content
    assert user_message.index("<document>") < user_message.index("<chunk>")


def test_llm_failure_returns_error(monkeypatch):
    document = _document("# 장애 대응\n결제 API 타임아웃이 발생했다.")

    result = _run_contextualize(monkeypatch, _FakeLLM(error=RuntimeError("timeout")), document)

    assert "error" in result
    assert "contextualized_chunks" not in result


# ---------------------------------------------------------------
# embed
# ---------------------------------------------------------------


def _contextualized(document_id, count: int) -> list[dict]:
    return [
        {
            "document_id": document_id,
            "chunk_index": i,
            "location": "",
            "content": f"본문 {i}",
            "search_text": f"검색용 {i}",
        }
        for i in range(count)
    ]


def test_embed_calls_kure_in_batches_of_eight(monkeypatch):
    calls: list[list[str]] = []

    def fake_embed_documents(texts):
        calls.append(texts)
        return [[float(len(calls))] for _ in texts]

    monkeypatch.setattr(embed_module, "embed_documents", fake_embed_documents)
    chunks = _contextualized(uuid4(), 19)

    result = asyncio.run(embed({"contextualized_chunks": chunks}))

    assert [len(texts) for texts in calls] == [8, 8, 3]
    assert calls[0][0] == "검색용 0"
    assert [c["embedding"] for c in result["embedded_chunks"]][:9] == [[1.0]] * 8 + [[2.0]]


def test_embed_failure_returns_error(monkeypatch):
    def fail(texts):
        raise RuntimeError("kure down")

    monkeypatch.setattr(embed_module, "embed_documents", fail)

    result = asyncio.run(embed({"contextualized_chunks": _contextualized(uuid4(), 3)}))

    assert "error" in result


# ---------------------------------------------------------------
# save
# ---------------------------------------------------------------


def test_save_replaces_chunks_per_document(monkeypatch):
    calls: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(
        save_module.repository, "replace_chunks", lambda doc_id, rows: calls.append((doc_id, rows))
    )
    with_chunks, without_chunks = uuid4(), uuid4()
    embedded = [{**c, "embedding": [0.1]} for c in _contextualized(with_chunks, 2)]

    result = asyncio.run(
        save(
            {
                "documents": [{"id": with_chunks}, {"id": without_chunks}],
                "embedded_chunks": embedded,
            }
        )
    )

    assert result["chunk_counts"] == {str(with_chunks): 2, str(without_chunks): 0}
    # 청크가 0개인 문서도 호출해서 이전 청크를 지운다
    assert [doc_id for doc_id, _ in calls] == [str(with_chunks), str(without_chunks)]
    row = calls[0][1][0]
    assert row["document_id"] == str(with_chunks)
    assert row["location"] is None
    assert row["search_text"] == "검색용 0"
    assert row["embedding"] == [0.1]


# ---------------------------------------------------------------
# graph
# ---------------------------------------------------------------


def test_graph_stops_after_error(monkeypatch):
    def fail_download(path):
        raise RuntimeError("not found")

    monkeypatch.setattr(parse_module.documents_repository, "download_file", fail_download)
    called = []
    monkeypatch.setattr(save_module.repository, "replace_chunks", lambda *a: called.append(a))
    document = {
        "id": uuid4(),
        "title": "문서",
        "storage_path": "job/file.txt",
        "original_filename": "file.txt",
    }

    result = asyncio.run(build_graph().ainvoke({"documents": [document]}))

    assert "file.txt" in result["error"]
    assert called == []


def test_graph_runs_all_nodes(monkeypatch):
    monkeypatch.setattr(
        parse_module.documents_repository,
        "download_file",
        lambda path: "# 장애 대응\n결제 API 타임아웃이 발생했다.".encode(),
    )
    monkeypatch.setattr(contextualize_module, "get_llm", lambda: _FakeLLM())
    monkeypatch.setattr(embed_module, "embed_documents", lambda texts: [[0.0] for _ in texts])
    saved = []
    monkeypatch.setattr(
        save_module.repository, "replace_chunks", lambda doc_id, rows: saved.extend(rows)
    )
    document_id = uuid4()
    document = {
        "id": document_id,
        "title": "결제 회고",
        "storage_path": "job/file.txt",
        "original_filename": "file.txt",
    }

    result = asyncio.run(build_graph().ainvoke({"documents": [document]}))

    assert not result.get("error")
    assert result["chunk_counts"] == {str(document_id): 1}
    assert saved[0]["search_text"].startswith("결제 회고 · 장애 대응\n")


# ---------------------------------------------------------------
# 인덱싱 엔드포인트 (POST /api/baseline/documents/index)
# ---------------------------------------------------------------

INDEX_URL = "/api/baseline/documents/index"


class _FakeGraph:
    def __init__(self, result: dict):
        self.result = result
        self.inputs: list[dict] = []

    async def ainvoke(self, state):
        self.inputs.append(state)
        return self.result


def _document_row(document_id) -> dict:
    return {
        "id": str(document_id),
        "title": "문서",
        "storage_path": f"job/{document_id}.txt",
        "original_filename": "file.txt",
    }


def _patch_index(monkeypatch, rows: list[dict], graph: _FakeGraph) -> None:
    monkeypatch.setattr(service_module.repository, "get_documents", lambda ids: rows)
    monkeypatch.setattr(service_module, "get_graph", lambda: graph)


def test_index_returns_chunk_counts_in_request_order(client, monkeypatch):
    first, second = uuid4(), uuid4()
    graph = _FakeGraph({"chunk_counts": {str(first): 3, str(second): 0}})
    # DB는 요청과 다른 순서로 돌려줄 수 있다
    _patch_index(monkeypatch, [_document_row(second), _document_row(first)], graph)

    response = client.post(INDEX_URL, json={"documentIds": [str(first), str(second)]})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"documentId": str(first), "chunkCount": 3},
            {"documentId": str(second), "chunkCount": 0},
        ]
    }
    assert [d["title"] for d in graph.inputs[0]["documents"]] == ["문서", "문서"]


def test_index_processes_duplicate_ids_once(client, monkeypatch):
    document_id = uuid4()
    graph = _FakeGraph({"chunk_counts": {str(document_id): 1}})
    _patch_index(monkeypatch, [_document_row(document_id)], graph)

    response = client.post(INDEX_URL, json={"documentIds": [str(document_id)] * 2})

    assert response.status_code == 200
    assert len(graph.inputs[0]["documents"]) == 1
    assert len(response.json()["items"]) == 1


def test_index_missing_document_returns_404_without_running_graph(client, monkeypatch):
    existing, missing = uuid4(), uuid4()
    graph = _FakeGraph({"chunk_counts": {}})
    _patch_index(monkeypatch, [_document_row(existing)], graph)

    response = client.post(INDEX_URL, json={"documentIds": [str(existing), str(missing)]})

    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]
    assert graph.inputs == []


def test_index_graph_error_returns_500(client, monkeypatch):
    document_id = uuid4()
    graph = _FakeGraph({"error": "'file.txt' 파싱 실패: not found"})
    _patch_index(monkeypatch, [_document_row(document_id)], graph)

    response = client.post(INDEX_URL, json={"documentIds": [str(document_id)]})

    assert response.status_code == 500
    assert "파싱 실패" in response.json()["detail"]


def test_index_empty_list_returns_422(client):
    response = client.post(INDEX_URL, json={"documentIds": []})

    assert response.status_code == 422


def test_index_route_is_registered(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert INDEX_URL in paths
