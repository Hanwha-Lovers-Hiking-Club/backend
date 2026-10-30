# 챗봇 답변 생성 테스트. LLM은 가짜로 바꿔 프롬프트 구성과 스트리밍 처리만 본다.
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.domains.chat import generator
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


async def test_streams_non_empty_chunks(monkeypatch, make_card):
    llm = FakeStreamLLM(["하이브리드 ", "", "검색을 썼어요."])
    monkeypatch.setattr(generator, "get_llm", lambda: llm)

    tokens = await _collect(generator.generate_answer("질문", [], "general", [make_card()]))

    assert tokens == ["하이브리드 ", "검색을 썼어요."]


async def test_prompt_has_intent_guidance_history_and_cards(monkeypatch, make_card):
    llm = FakeStreamLLM(["답"])
    monkeypatch.setattr(generator, "get_llm", lambda: llm)
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


async def test_history_is_trimmed_to_limit(monkeypatch, make_card):
    llm = FakeStreamLLM(["답"])
    monkeypatch.setattr(generator, "get_llm", lambda: llm)
    history = [ChatMessage(role="user", content=f"질문 {i}") for i in range(30)]

    await _collect(generator.generate_answer("질문", history, "general", [make_card()]))

    # 시스템 프롬프트 + 최근 대화 + 마지막 질문
    assert len(llm.messages) == 1 + generator.HISTORY_LIMIT + 1
    assert llm.messages[1].content == f"질문 {30 - generator.HISTORY_LIMIT}"


async def test_no_cards_answers_with_notice_without_calling_llm(monkeypatch):
    def forbidden():
        raise AssertionError("카드가 없으면 LLM을 부르면 안 된다")

    monkeypatch.setattr(generator, "get_llm", forbidden)

    tokens = await _collect(generator.generate_answer("질문", [], "general", []))

    assert tokens == [generator.NO_CARD_MESSAGE]
