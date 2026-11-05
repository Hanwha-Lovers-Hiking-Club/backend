# 챗봇 로직: 질문 분석 -> 원문·지식카드 검색 -> (검증) -> 답변 생성을 SSE 이벤트로 흘려보낸다.
# 각 단계의 구현은 intent.py / retriever.py / verifier.py / generator.py에 있고, 여기서는 순서와
# 분기만 정한다.
#   A. 개발 도메인 밖 질문        -> 고정 안내 문구 (검색하지 않음)
#   B. 자료로 답할 수 없음         -> 고정 안내 문구 (LLM 답변 생성 안 함)
#   D. 원문만 있고 관련 카드 없음  -> 원문으로 답변, 카드 참조는 빈 목록 (프론트가 카드 영역 숨김)
#   E. 원문과 관련 카드 모두 있음  -> 원문 + 카드로 답변, 카드 참조 표시
# (C는 D와 E에 공통인 "원문 선별" 단계라 따로 분기하지 않는다.) 정보가 너무 부족하면 되묻는다.
import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi.sse import ServerSentEvent

from app.config import settings
from app.core.sse import to_sse
from app.domains.chat.generator import NO_DATA_MESSAGE, OFF_TOPIC_MESSAGE, generate_answer
from app.domains.chat.intent import understand_intent
from app.domains.chat.models import ChunkCandidate, VerifiedCandidate
from app.domains.chat.retriever import find_linked_evidence, search
from app.domains.chat.schemas import (
    ChatCompletedEvent,
    ChatErrorEvent,
    ChatMessage,
    ChatReference,
    ChatReferencesEvent,
    ChatSource,
    ChatSourceDocument,
    ChatTokenEvent,
)
from app.domains.chat.verifier import skip_verification, verify

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


def _to_reference(verified: VerifiedCandidate, chunks: list[ChunkCandidate]) -> ChatReference:
    # 참조 하나 = 카드 하나 + 그 카드의 근거 하나. 답변에 쓴 청크(chunks)에 들어 있는 근거를
    # 우선 보여주고, 없으면 첫 번째 문서의 첫 번째 근거를 보여준다
    card = verified.candidate.card
    source_document, evidence = find_linked_evidence(card, chunks) or (
        card.evidences[0],
        card.evidences[0].items[0],
    )
    return ChatReference(
        id=evidence.id,
        type="knowledge-card",
        label=card.title,
        card=card,
        reason=verified.reason,
        document=ChatSourceDocument(
            id=source_document.document_id,
            document_title=source_document.document_title,
            location=evidence.location,
            excerpt=evidence.excerpt,
        ),
    )


def _to_source(chunk: ChunkCandidate) -> ChatSource:
    return ChatSource(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        location=chunk.location,
        excerpt=chunk.content,
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

    # A. 개발 도메인 여부는 검색 결과와 상관없이 질문만 보고 판단한다 (자료가 없는 개발 질문과 구분)
    if not intent.is_dev_domain:
        for event in _text_events(OFF_TOPIC_MESSAGE):
            yield event
        return

    # 검색하기엔 정보가 부족하면 검색하지 않고 되묻는다 (되묻는 문장도 token 이벤트로 보낸다)
    if intent.needs_clarification and intent.clarifying_question:
        for event in _text_events(intent.clarifying_question):
            yield event
        return

    retrieval = await asyncio.wait_for(search(intent), timeout=SEARCH_TIMEOUT_SECONDS)
    if settings.CHAT_VERIFY_ENABLED:
        verification = await verify(intent.search_query, retrieval)
    else:
        verification = skip_verification(retrieval)

    # B. 답할 수 있는지는 검색 유사도가 아니라 검증 단계가 자료 내용을 보고 정한다
    if not verification.answerable:
        for event in _text_events(NO_DATA_MESSAGE):
            yield event
        return

    # D/E. 근거가 하나도 없는 카드는 참조로 보여줄 수 없다 (카드당 근거 1개 이상은 앱 로직이
    # 보장하지만 방어한다). 관련 카드가 없으면 빈 목록이 되어 원문만으로 답한다(D)
    references = [
        _to_reference(v, verification.chunks)
        for v in verification.cards
        if v.candidate.card.evidences
    ][:MAX_REFERENCES]
    cards = [reference.card for reference in references]
    # 이전 대화는 후속 질문일 때만 넘긴다. 새 주제 질문에 이전 대화가 섞이지 않게 한다
    generation_history = history if intent.is_follow_up else []

    async for event in _stream_until(
        generate_answer(
            message,
            generation_history,
            intent.kind,
            cards,
            chunks=verification.chunks,
            conflicts=verification.conflicts,
        ),
        deadline,
    ):
        yield event
    yield to_sse(
        "references",
        ChatReferencesEvent(items=references, sources=[_to_source(c) for c in verification.chunks]),
    )


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
    # 고정 문구로 답한 경우에도 피드백을 받을 수 있도록 모든 답변에 식별자를 붙인다
    yield to_sse("completed", ChatCompletedEvent(message_id=uuid.uuid4()))
