# 1단계: 질문 의도 파악. 의도 분류와 검색 문장 정리, 정보가 부족할 때의 역질문 여부를 한 번의 LLM
# 호출로 판단한다. 이후 단계(어떤 임베딩으로 검색할지, 어떤 답변 지침을 쓸지)가 이 결과로 갈린다.
from pathlib import Path

from app.domains.chat.models import Intent
from app.domains.chat.schemas import ChatMessage
from app.domains.documents.pipeline.llm import get_llm

_PROMPT = (Path(__file__).parent / "prompts" / "intent.md").read_text(encoding="utf-8")

# 의도 파악에 쓰는 이전 대화 개수. 너무 길면 비용만 늘고 검색 문장 정리에는 최근 대화면 충분하다
HISTORY_LIMIT = 6

ROLE_LABELS = {"user": "사용자", "assistant": "챗봇"}


def _format_conversation(message: str, history: list[ChatMessage]) -> str:
    lines = [f"{ROLE_LABELS[m.role]}: {m.content}" for m in history[-HISTORY_LIMIT:]]
    lines.append(f"사용자(마지막 질문): {message}")
    return "\n".join(lines)


async def understand_intent(message: str, history: list[ChatMessage]) -> Intent:
    llm = get_llm().with_structured_output(Intent)
    return await llm.ainvoke(f"{_PROMPT}{_format_conversation(message, history)}")
