# 챗봇 로직: 질문 의도 파악 -> 지식카드 검색 -> (검증) -> 답변 생성을 SSE 이벤트로 흘려보낸다.
# 각 단계의 구현은 intent.py / retriever.py / verifier.py / generator.py에 있고, 여기서는 순서와
# 분기(범위 밖 질문, 역질문, 카드 없음)만 정한다.
import asyncio
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

# 질문을 받은 뒤 답변이 끝날 때까지의 최대 시간. 실측은 보통 9~17초지만, LLM 클라이언트는 한 호출에
# 120초 타임아웃 + SDK 재시도 2회라서 응답이 늦어지면 몇 분씩 멈출 수 있다.
# 그래서 단계마다 상한을 둔다.
# 질문 분석/검색은 대신할 방법이 없어 넘기면 error 이벤트로 끝내고, 검증은 넘기면 검증 없이 진행하며
# (verifier.VERIFY_TIMEOUT_SECONDS), 답변 생성은 남은 시간까지만 스트리밍한다.
ANSWER_TIME_LIMIT_SECONDS = 30
INTENT_TIMEOUT_SECONDS = 6  # 실측 1.3~3초
SEARCH_TIMEOUT_SECONDS = 6  # 실측 0.7~1.2초 (서버 시작 후 첫 검색은 원문 적재로 약 4초)
# 답변이 시간 안에 끝나지 않아 중간에 끊을 때 뒤에 붙이는 안내
TRUNCATED_NOTICE = (
    "\n\n요청하신 내용을 처리하는 데 시간이 예상보다 오래 걸리고 있습니다. "
    "잠시 후 다시 시도해 주세요."
)


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


async def _stream_until(
    tokens: AsyncIterator[str], deadline: float
) -> AsyncIterator[ServerSentEvent]:
    # 토큰마다 남은 시간만큼만 기다린다. async generator 안에서 yield를 감싸는 asyncio.timeout은
    # 소비하는 쪽까지 취소할 수 있어서 쓰지 않는다
    loop = asyncio.get_running_loop()
    streamed = False
    iterator = aiter(tokens)
    while True:
        try:
            token = await asyncio.wait_for(anext(iterator), timeout=deadline - loop.time())
        except StopAsyncIteration:
            return
        except TimeoutError:
            # 한 글자도 못 보냈으면 답변이 없는 것이라 실패로 처리한다
            if not streamed:
                raise
            logger.warning(
                "답변 시간 상한(%s초)에 걸려 답변을 중간에 끊음", ANSWER_TIME_LIMIT_SECONDS
            )
            yield to_sse("token", ChatTokenEvent(content=TRUNCATED_NOTICE))
            return
        streamed = True
        yield to_sse("token", ChatTokenEvent(content=token))


async def _answer_events(
    message: str, history: list[ChatMessage]
) -> AsyncIterator[ServerSentEvent]:
    deadline = asyncio.get_running_loop().time() + ANSWER_TIME_LIMIT_SECONDS
    intent = await asyncio.wait_for(
        understand_intent(message, history), timeout=INTENT_TIMEOUT_SECONDS
    )

    # 개발 도메인 여부는 검색 결과와 상관없이 질문만 보고 판단한다 (자료가 없는 개발 질문과 구분)
    if not intent.is_dev_domain:
        for event in _text_events(OFF_TOPIC_MESSAGE):
            yield event
        return

    # 검색하기엔 정보가 부족하면 검색하지 않고 되묻는다 (되묻는 문장도 token 이벤트로 보낸다)
    if intent.needs_clarification and intent.clarifying_question:
        for event in _text_events(intent.clarifying_question):
            yield event
        return

    candidates = await asyncio.wait_for(search_cards(intent), timeout=SEARCH_TIMEOUT_SECONDS)
    if settings.CHAT_VERIFY_ENABLED:
        verified = await verify_candidates(intent.search_query, candidates)
    else:
        verified = unverified(candidates)

    # 근거가 하나도 없는 카드는 참조로 보여줄 수 없다 (카드당 근거 1개 이상은 앱 로직이 보장하지만
    # 방어한다)
    references = [_to_reference(v) for v in verified if v.candidate.card.evidences][:MAX_REFERENCES]

    cards = [reference.card for reference in references]
    async for event in _stream_until(
        generate_answer(message, history, intent.kind, cards), deadline
    ):
        yield event
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
