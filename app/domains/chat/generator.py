# 4단계: 답변 생성. 검증을 통과한 지식카드만 컨텍스트로 넣어 LLM 답변을 토큰 단위로 스트리밍한다.
# 챗봇의 성격(시스템 프롬프트)은 prompts/answer.md와 아래 의도별 지침이 정한다.
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.domains.chat.card_context import format_cards
from app.domains.chat.models import IntentKind
from app.domains.chat.schemas import ChatMessage
from app.domains.documents.pipeline.llm import get_llm
from app.domains.knowledge_cards.schemas import KnowledgeCard

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "answer.md").read_text(encoding="utf-8")

# LLM을 부르지 않고 고정 문구로 답하는 경우 (답변할 수 없는 상황의 예외처리)
NO_CARD_MESSAGE = (
    "질문과 관련된 지식카드를 찾지 못했어요. "
    "프로젝트, 기술 스택, 겪은 문제를 조금 더 구체적으로 알려주시면 다시 찾아볼게요."
)
OFF_TOPIC_MESSAGE = (
    "저는 프로젝트 산출물에서 정리한 지식카드를 바탕으로 답하는 챗봇이라 "
    "그 질문에는 답하기 어려워요. "
    "비슷한 프로젝트, 비슷한 이슈, 해결 방법, 교훈, 기술 스택에 대해 물어봐 주세요."
)

HISTORY_LIMIT = 10

# 의도에 따라 답변 구성이 달라진다 (명세서: 의도에 따라 프롬프트를 다르게 사용)
INTENT_GUIDANCE: dict[IntentKind, str] = {
    "similar_project": (
        "비슷한 프로젝트를 추천하는 질문입니다. 프로젝트(또는 카드)마다 질문과 어떤 점이 비슷한지 "
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
        "교훈을 찾는 질문입니다. 카드들에서 재사용할 수 있는 교훈을 뽑아, 어떤 경험에서 나온 "
        "교훈인지 함께 답하세요."
    ),
    "tech_stack": (
        "특정 기술 스택과 관련된 질문입니다. 그 기술을 쓴 카드의 이슈와 대응을 중심으로 답하세요."
    ),
    "general": "질문에 직접 답하는 데 도움이 되는 카드 내용을 골라 답하세요.",
    "off_topic": "",
}


def _build_messages(
    message: str, history: list[ChatMessage], kind: IntentKind, cards: list[KnowledgeCard]
) -> list[BaseMessage]:
    system = f"{_SYSTEM_PROMPT}{INTENT_GUIDANCE[kind]}"
    messages: list[BaseMessage] = [SystemMessage(content=system)]
    for item in history[-HISTORY_LIMIT:]:
        message_type = HumanMessage if item.role == "user" else AIMessage
        messages.append(message_type(content=item.content))
    messages.append(
        HumanMessage(content=f"## 참고 지식카드\n\n{format_cards(cards)}\n\n## 질문\n{message}")
    )
    return messages


async def generate_answer(
    message: str, history: list[ChatMessage], kind: IntentKind, cards: list[KnowledgeCard]
) -> AsyncIterator[str]:
    # 카드가 하나도 없으면 LLM이 근거 없이 지어내지 않도록 호출하지 않고 고정 문구로 답한다
    if not cards:
        yield NO_CARD_MESSAGE
        return

    async for chunk in get_llm().astream(_build_messages(message, history, kind, cards)):
        if chunk.content:
            yield chunk.content
