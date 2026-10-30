# 챗봇 로직: 질문 의도 파악 -> 지식카드 검색 -> (검증) -> 답변 생성을 SSE 이벤트로 흘려보낸다.
# 각 단계의 구현은 intent.py / retriever.py / verifier.py / generator.py에 있고, 여기서는 순서와
# 분기(범위 밖 질문, 역질문, 카드 없음)만 정한다.
import logging
from collections.abc import AsyncIterator

from fastapi.sse import ServerSentEvent

from app.config import settings
from app.core.sse import to_sse
from app.domains.chat.generator import OFF_TOPIC_MESSAGE, generate_answer
from app.domains.chat.intent import understand_intent
from app.domains.chat.models import VerifiedCandidate
from app.domains.chat.retriever import search_cards
from app.domains.chat.schemas import (
    ChatCompletedEvent,
    ChatErrorEvent,
    ChatMessage,
    ChatReference,
    ChatReferencesEvent,
    ChatSourceDocument,
    ChatTokenEvent,
)
from app.domains.chat.verifier import unverified, verify_candidates

logger = logging.getLogger(__name__)

CHAT_FAILED_MESSAGE = "답변을 생성하지 못했습니다."
# 답변에 붙이는 참조(지식카드) 최대 개수. 많으면 답변이 산만해지고 화면 참조 버튼도 길어진다
MAX_REFERENCES = 3


def _to_reference(verified: VerifiedCandidate) -> ChatReference:
    # 참조 하나 = 카드 하나 + 그 카드의 근거 하나. 근거가 여러 개면 첫 번째를 보여준다
    card = verified.candidate.card
    evidence = card.evidences[0]
    return ChatReference(
        id=evidence.id,
        type="knowledge-card",
        label=card.title,
        card=card,
        reason=verified.reason,
        document=ChatSourceDocument(
            id=evidence.document_id,
            document_title=evidence.document_title,
            location=evidence.location,
            excerpt=evidence.excerpt,
        ),
    )


def _text_events(text: str) -> list[ServerSentEvent]:
    # LLM 없이 고정 문구로 답하는 경우. 참조 없이 token -> references(빈 목록)로 같은 순서를 지킨다
    return [
        to_sse("token", ChatTokenEvent(content=text)),
        to_sse("references", ChatReferencesEvent(items=[])),
    ]


async def _answer_events(
    message: str, history: list[ChatMessage]
) -> AsyncIterator[ServerSentEvent]:
    intent = await understand_intent(message, history)

    if intent.kind == "off_topic":
        for event in _text_events(OFF_TOPIC_MESSAGE):
            yield event
        return

    # 검색하기엔 정보가 부족하면 검색하지 않고 되묻는다 (되묻는 문장도 token 이벤트로 보낸다)
    if intent.needs_clarification and intent.clarifying_question:
        for event in _text_events(intent.clarifying_question):
            yield event
        return

    candidates = await search_cards(intent)
    if settings.CHAT_VERIFY_ENABLED:
        verified = await verify_candidates(intent.search_query, candidates)
    else:
        verified = unverified(candidates)

    # 근거가 하나도 없는 카드는 참조로 보여줄 수 없다 (카드당 근거 1개 이상은 앱 로직이 보장하지만
    # 방어한다)
    references = [_to_reference(v) for v in verified if v.candidate.card.evidences][:MAX_REFERENCES]

    cards = [reference.card for reference in references]
    async for token in generate_answer(message, history, intent.kind, cards):
        yield to_sse("token", ChatTokenEvent(content=token))
    yield to_sse("references", ChatReferencesEvent(items=references))


async def chat_stream(message: str, history: list[ChatMessage]) -> AsyncIterator[ServerSentEvent]:
    # token(여러 번) -> references -> completed 순서로 보낸다.
    # 스트림이 시작된 뒤엔 HTTP 상태코드를 바꿀 수 없어서, 실패는 error 이벤트로 알리고 끝낸다
    try:
        async for event in _answer_events(message, history):
            yield event
    except Exception:
        logger.exception("챗봇 답변 생성 실패")
        yield to_sse("error", ChatErrorEvent(message=CHAT_FAILED_MESSAGE))
        return
    yield to_sse("completed", ChatCompletedEvent())
