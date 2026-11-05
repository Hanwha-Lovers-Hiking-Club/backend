# 챗봇 질문 분석 단계 테스트. LLM은 가짜로 바꿔 프롬프트에 들어가는 대화 모양만 본다.
import pytest

from app.domains.chat import intent
from app.domains.chat.models import Intent
from app.domains.chat.schemas import ChatMessage

pytestmark = pytest.mark.anyio


def _result() -> Intent:
    return Intent(
        is_dev_domain=True,
        kind="solution",
        is_follow_up=True,
        search_query="예약 대기실 다음 버전에서 바뀐 점",
        key_terms=["대기실"],
        needs_clarification=False,
        clarifying_question=None,
    )


class FakeLLM:
    def __init__(self):
        self.prompt = None

    def with_structured_output(self, schema):
        assert schema is Intent
        return self

    async def ainvoke(self, prompt):
        self.prompt = prompt
        return _result()


def test_conversation_ends_with_last_question():
    history = [
        ChatMessage(role="user", content="대기실을 왜 도입했어?"),
        ChatMessage(role="assistant", content="부하를 줄이려고요."),
    ]

    text = intent._format_conversation("그 다음 버전은?", history)

    assert text.splitlines() == [
        "사용자: 대기실을 왜 도입했어?",
        "챗봇: 부하를 줄이려고요.",
        "사용자(마지막 질문): 그 다음 버전은?",
    ]


def test_long_assistant_answers_are_cut_but_user_messages_are_not():
    long_text = "가" * (intent.ASSISTANT_PREVIEW_CHARS + 50)
    history = [
        ChatMessage(role="user", content=long_text),
        ChatMessage(role="assistant", content=long_text),
    ]

    user_line, assistant_line, _ = intent._format_conversation("질문", history).splitlines()

    assert user_line == f"사용자: {long_text}"
    assert assistant_line == f"챗봇: {'가' * intent.ASSISTANT_PREVIEW_CHARS}...(생략)"


def test_only_recent_history_is_used():
    history = [ChatMessage(role="user", content=f"질문 {n}") for n in range(10)]

    lines = intent._format_conversation("마지막", history).splitlines()

    assert len(lines) == intent.HISTORY_LIMIT + 1
    assert lines[0] == "사용자: 질문 4"


async def test_understand_intent_sends_prompt_and_conversation(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(intent, "get_llm", lambda: llm)

    result = await intent.understand_intent("그 다음 버전은?", [])

    assert result == _result()
    assert llm.prompt.startswith(intent._PROMPT)
    assert llm.prompt.endswith("사용자(마지막 질문): 그 다음 버전은?")
