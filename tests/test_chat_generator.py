# 챗봇 답변 생성 테스트. LLM은 가짜로 바꿔 프롬프트 구성과 스트리밍 처리만 본다.
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.domains.chat import generator
from app.domains.chat.models import ChunkCandidate
from app.domains.chat.schemas import ChatMessage

pytestmark = pytest.mark.anyio


class FakeStreamLLM:
    def __init__(self, chunks):
        self.chunks = chunks
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        for content in self.chunks:
            yield SimpleNamespace(content=content)


async def _collect(stream) -> list[str]:
    return [token async for token in stream]


def _chunk(
    content="재시도 간격을 지수 백오프로 바꿨다.", before=None, after=None
) -> ChunkCandidate:
    return ChunkCandidate(
        id="chunk-1",
        document_id="doc-1",
        document_title="결제 장애 회고",
        chunk_index=3,
        location="2장",
        content=content,
        before=before,
        after=after,
        score=0.02,
        similarity=0.5,
    )


def _use(monkeypatch, llm):
    monkeypatch.setattr(generator, "get_llm", lambda: llm)


async def test_streams_non_empty_chunks(monkeypatch, make_card):
    _use(monkeypatch, FakeStreamLLM(["하이브리드 ", "", "검색을 썼어요."]))

    tokens = await _collect(generator.generate_answer("질문", [], "general", [make_card()]))

    assert tokens == ["하이브리드 ", "검색을 썼어요."]


async def test_prompt_has_intent_guidance_history_and_cards(monkeypatch, make_card):
    llm = FakeStreamLLM(["답"])
    _use(monkeypatch, llm)
    history = [
        ChatMessage(role="user", content="이전 질문"),
        ChatMessage(role="assistant", content="이전 답변"),
    ]

    await _collect(
        generator.generate_answer("지금 질문", history, "solution", [make_card("A 카드")])
    )

    system, previous_user, previous_assistant, final = llm.messages
    assert isinstance(system, SystemMessage)
    assert generator.INTENT_GUIDANCE["solution"] in system.content
    assert isinstance(previous_user, HumanMessage) and previous_user.content == "이전 질문"
    assert isinstance(previous_assistant, AIMessage) and previous_assistant.content == "이전 답변"
    assert "[카드 1] A 카드" in final.content
    assert final.content.endswith("지금 질문")


async def test_prompt_puts_chunks_with_neighbors_before_cards(monkeypatch, make_card):
    llm = FakeStreamLLM(["답"])
    _use(monkeypatch, llm)

    await _collect(
        generator.generate_answer(
            "질문",
            [],
            "solution",
            [make_card("A 카드")],
            chunks=[_chunk(before="장애 당시 상황", after="이후 실패율 감소")],
        )
    )

    final = llm.messages[-1].content
    assert final.index("## 참고 원문") < final.index("## 참고 지식카드")
    assert (
        "[원문 1] 문서: 결제 장애 회고 · 위치: 2장\n(앞 내용) 장애 당시 상황\n"
        "재시도 간격을 지수 백오프로 바꿨다.\n(뒤 내용) 이후 실패율 감소"
    ) in final
    assert "## 원문 간 상충" not in final


async def test_chunks_only_answers_with_cards_marked_none(monkeypatch):
    # 유형 D: 원문만 있고 카드가 없어도 LLM으로 답한다
    llm = FakeStreamLLM(["원문 기반 답"])
    _use(monkeypatch, llm)

    tokens = await _collect(generator.generate_answer("질문", [], "general", [], chunks=[_chunk()]))

    assert tokens == ["원문 기반 답"]
    assert "## 참고 지식카드\n\n(없음)" in llm.messages[-1].content


async def test_conflicts_are_added_to_prompt(monkeypatch):
    llm = FakeStreamLLM(["답"])
    _use(monkeypatch, llm)

    await _collect(
        generator.generate_answer(
            "질문", [], "general", [], chunks=[_chunk()], conflicts="v0.9는 권고, v1.0은 철회"
        )
    )

    final = llm.messages[-1].content
    assert "## 원문 간 상충\nv0.9는 권고, v1.0은 철회" in final
    assert final.index("## 원문 간 상충") < final.index("## 질문")


async def test_history_is_trimmed_to_limit(monkeypatch, make_card):
    llm = FakeStreamLLM(["답"])
    _use(monkeypatch, llm)
    history = [ChatMessage(role="user", content=f"질문 {i}") for i in range(30)]

    await _collect(generator.generate_answer("질문", history, "general", [make_card()]))

    # 시스템 프롬프트 + 최근 대화 + 마지막 질문
    assert len(llm.messages) == 1 + generator.HISTORY_LIMIT + 1
    assert llm.messages[1].content == f"질문 {30 - generator.HISTORY_LIMIT}"


async def test_no_data_answers_with_notice_without_calling_llm(monkeypatch):
    def forbidden():
        raise AssertionError("자료가 없으면 LLM을 부르면 안 된다")

    monkeypatch.setattr(generator, "get_llm", forbidden)

    tokens = await _collect(generator.generate_answer("질문", [], "general", [], chunks=[]))

    assert tokens == [generator.NO_DATA_MESSAGE]


def test_fixed_messages_match_planned_wording():
    # 기획에서 정한 문구 그대로여야 한다
    assert generator.OFF_TOPIC_MESSAGE == (
        "죄송합니다. 해당 챗봇은 개발 도메인과 관련된 질문에 답변하도록 설계되어 있습니다. "
        "개발 기술 및 관련 개념에 대해 질문해 주세요."
    )
    assert generator.NO_DATA_MESSAGE == (
        "현재 보유한 자료에서는 해당 내용에 대한 정보를 확인할 수 없습니다. "
        "정확한 답변을 위해 관련 사수 또는 담당팀에 문의해 주세요."
    )


def test_answer_prompt_uses_same_no_data_wording():
    # 자료가 질문과 무관할 때 LLM도 같은 문구로 답하도록 프롬프트에 적어 뒀다 (줄바꿈 무시하고 비교)
    prompt = "".join(generator._SYSTEM_PROMPT.split())
    assert "".join(generator.NO_DATA_MESSAGE.split()) in prompt
