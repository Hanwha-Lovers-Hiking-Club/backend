# 노드: 대화 내역을 반영해 독립 질문 1개와 다른 표현 질문 2개를 만든다 (multi-query).
# 표현이 다른 질문으로 여러 번 검색하면, 질문과 문서의 단어가 달라서 놓치는 청크를 줄일 수 있다.
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.domains.baseline_chat.pipeline.state import RetrievalState
from app.domains.chat.schemas import ChatMessage
from app.domains.documents.pipeline.llm import get_llm

_PROMPT = (Path(__file__).parent.parent / "prompts" / "rewrite.md").read_text(encoding="utf-8")

ALTERNATIVE_COUNT = 2
# 질문 재작성에 넘기는 최근 대화 수. 오래된 대화는 현재 질문과 관계가 적다
MAX_HISTORY_MESSAGES = 6

_ROLE_LABELS = {"user": "사용자", "assistant": "챗봇"}


class RewrittenQueries(BaseModel):
    standalone: str = Field(description="이전 대화 없이도 뜻이 통하는 독립 질문")
    alternatives: list[str] = Field(description="같은 정보를 찾는 다른 표현의 질문 2개")


def format_history(history: list[ChatMessage]) -> str:
    recent = history[-MAX_HISTORY_MESSAGES:]
    return "\n".join(f"{_ROLE_LABELS[m.role]}: {m.content}" for m in recent)


def build_messages(message: str, history: list[ChatMessage]) -> list:
    return [
        SystemMessage(content=_PROMPT),
        HumanMessage(
            content=f"<history>\n{format_history(history)}\n</history>\n\n"
            f"<question>\n{message}\n</question>"
        ),
    ]


async def rewrite(state: RetrievalState) -> dict:
    llm = get_llm().with_structured_output(RewrittenQueries)
    result: RewrittenQueries = await llm.ainvoke(
        build_messages(state["message"], state.get("history", []))
    )

    # 독립 질문을 맨 앞에 두고, 빈 문자열과 중복을 뺀다
    candidates = [result.standalone, *result.alternatives[:ALTERNATIVE_COUNT]]
    queries = list(dict.fromkeys(q.strip() for q in candidates if q.strip()))
    # LLM이 빈 결과를 주면 원래 질문으로 검색한다
    return {"queries": queries or [state["message"]]}
