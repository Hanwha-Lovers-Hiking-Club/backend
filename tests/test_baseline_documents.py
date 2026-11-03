# 기본 RAG 문서 인덱싱 파이프라인 테스트 (청크 분할, 문맥 보강).
# 실제 OpenAI는 호출하지 않고 get_llm을 가짜 객체로 바꾼다.
import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.domains.baseline_documents.pipeline.nodes import contextualize as contextualize_module
from app.domains.baseline_documents.pipeline.nodes.chunk import (
    CHUNK_SIZE_TOKENS,
    chunk,
    split_document,
)
from app.domains.baseline_documents.pipeline.nodes.contextualize import (
    build_header,
    contextualize,
)
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
