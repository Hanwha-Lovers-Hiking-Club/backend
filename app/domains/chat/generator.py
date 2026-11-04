# 4단계: 답변 생성. 검증을 통과한 원문과 지식카드를 컨텍스트로 넣어 LLM 답변을 토큰 단위로
# 스트리밍한다. 원문이 사실 근거이고, 카드는 핵심 개념을 잡는 보조 자료다.
# 챗봇의 성격(시스템 프롬프트)은 prompts/answer.md와 아래 의도별 지침이 정한다.
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.domains.chat.card_context import format_cards, format_chunks
from app.domains.chat.models import ChunkCandidate, IntentKind
from app.domains.chat.schemas import ChatMessage
from app.domains.documents.pipeline.llm import get_llm
from app.domains.knowledge_cards.schemas import KnowledgeCard

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "answer.md").read_text(encoding="utf-8")

# LLM을 부르지 않고 고정 문구로 답하는 경우 (답변할 수 없는 상황의 예외처리).
# 문구는 기획에서 정한 그대로 쓴다
# 유형 B: 개발 질문이지만 보유 자료에 관련 정보가 없음
NO_DATA_MESSAGE = (
    "현재 보유한 자료에서는 해당 내용에 대한 정보를 확인할 수 없습니다. "
    "정확한 답변을 위해 관련 사수 또는 담당팀에 문의해 주세요."
)
# 유형 A: 개발 도메인과 무관한 질문
OFF_TOPIC_MESSAGE = (
    "죄송합니다. 해당 챗봇은 개발 도메인과 관련된 질문에 답변하도록 설계되어 있습니다. "
    "개발 기술 및 관련 개념에 대해 질문해 주세요."
)

# 이전 대화는 후속 질문일 때만 넘긴다(service.py). 지금 질문이 무엇을 가리키는지 알면 충분해서
# 최근 대화만 넣고, 이전 답변은 앞부분만 넣는다. 답변 근거는 매번 새로 검색한 자료다
HISTORY_LIMIT = 6
ASSISTANT_PREVIEW_CHARS = 500

_NONE = "(없음)"

# 의도에 따라 답변 구성이 달라진다 (명세서: 의도에 따라 프롬프트를 다르게 사용)
INTENT_GUIDANCE: dict[IntentKind, str] = {
    "similar_project": (
        "비슷한 프로젝트를 추천하는 질문입니다. 프로젝트마다 질문과 어떤 점이 비슷한지 "
        "중심으로 소개하세요."
    ),
    "similar_issue": (
        "비슷한 이슈를 찾는 질문입니다. 어떤 상황에서 어떤 문제가 있었고 원인이 무엇이었는지 "
        "중심으로 답하세요."
    ),
    "solution": (
        "해결 방법을 묻는 질문입니다. 어떻게 해결했는지, 왜 그 방법을 골랐는지, 결과가 어땠는지 "
        "순서로 답하세요."
    ),
    "similar_lesson": (
        "교훈을 찾는 질문입니다. 자료에서 재사용할 수 있는 교훈을 뽑아, 어떤 경험에서 나온 "
        "교훈인지 함께 답하세요."
    ),
    "tech_stack": (
        "특정 기술 스택과 관련된 질문입니다. 그 기술을 쓴 프로젝트의 이슈와 대응을 중심으로 "
        "답하세요."
    ),
    "concept": (
        "개념이나 원리를 묻는 질문입니다. 자료에 나온 설명과 실제 적용 사례를 바탕으로 설명하고, "
        "자료에 없는 일반적인 정의를 덧붙이지 마세요."
    ),
    "general": "질문에 직접 답하는 데 도움이 되는 자료 내용을 골라 답하세요.",
}


def _context(
    chunks: list[ChunkCandidate], cards: list[KnowledgeCard], conflicts: str | None
) -> str:
    # 원문은 앞뒤 청크까지 넣어 맥락을 준다. 상충은 검증 단계가 찾은 경우에만 붙인다
    parts = [
        f"## 참고 원문\n\n{format_chunks(chunks, with_neighbors=True) or _NONE}",
        f"## 참고 지식카드\n\n{format_cards(cards) or _NONE}",
    ]
    if conflicts:
        parts.append(f"## 원문 간 상충\n{conflicts}")
    return "\n\n".join(parts)


def _build_messages(
    message: str,
    history: list[ChatMessage],
    kind: IntentKind,
    chunks: list[ChunkCandidate],
    cards: list[KnowledgeCard],
    conflicts: str | None,
) -> list[BaseMessage]:
    system = f"{_SYSTEM_PROMPT}{INTENT_GUIDANCE[kind]}"
    messages: list[BaseMessage] = [SystemMessage(content=system)]
    for item in history[-HISTORY_LIMIT:]:
        if item.role == "user":
            messages.append(HumanMessage(content=item.content))
        elif len(item.content) > ASSISTANT_PREVIEW_CHARS:
            messages.append(AIMessage(content=item.content[:ASSISTANT_PREVIEW_CHARS] + "...(생략)"))
        else:
            messages.append(AIMessage(content=item.content))
    messages.append(
        HumanMessage(content=f"{_context(chunks, cards, conflicts)}\n\n## 질문\n{message}")
    )
    return messages


async def generate_answer(
    message: str,
    history: list[ChatMessage],
    kind: IntentKind,
    cards: list[KnowledgeCard],
    chunks: list[ChunkCandidate] | None = None,
    conflicts: str | None = None,
) -> AsyncIterator[str]:
    chunks = chunks or []
    # 자료가 하나도 없으면 LLM이 근거 없이 지어내지 않도록 호출하지 않고 고정 문구로 답한다
    if not chunks and not cards:
        yield NO_DATA_MESSAGE
        return

    messages = _build_messages(message, history, kind, chunks, cards, conflicts)
    async for part in get_llm().astream(messages):
        if part.content:
            yield part.content
