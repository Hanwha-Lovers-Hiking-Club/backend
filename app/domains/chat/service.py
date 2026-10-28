# 챗봇 로직: 질문에 맞는 지식카드를 찾고(검색) 답변을 만들어(생성) SSE 이벤트로 흘려보낸다.
# 지금 검색/생성은 mock이다.
# RAG가 준비되면 _retrieve_references, _generate_answer 두 함수만 교체한다.
import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi.sse import ServerSentEvent

from app.core.sse import to_sse
from app.domains.chat.schemas import (
    ChatCompletedEvent,
    ChatErrorEvent,
    ChatMessage,
    ChatReference,
    ChatReferencesEvent,
    ChatSourceDocument,
    ChatTokenEvent,
)
from app.domains.knowledge_cards.schemas import CardEvidence, KnowledgeCard

logger = logging.getLogger(__name__)

CHAT_FAILED_MESSAGE = "답변을 생성하지 못했습니다."
# mock 답변을 토큰 단위로 나눠 보낼 때 토큰 사이 간격(초). 스트리밍처럼 보이게 하는 용도
TOKEN_INTERVAL = 0.05

# mock 표시. 실제 답변/근거로 오인하지 않도록 답변 첫 토큰과 근거 reason에 붙인다
MOCK_LABEL = "[Mock]"
# DB에 없는 nil UUID라 실제 카드/문서와 겹치지 않는다
MOCK_ID = UUID(int=0)
MOCK_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _wait() -> None:
    await asyncio.sleep(TOKEN_INTERVAL)


async def _retrieve_references(message: str, history: list[ChatMessage]) -> list[ChatReference]:
    # mock: 실제로는 질문을 임베딩해 active 지식카드를 검색하고 근거(card_documents)를 붙인다.
    # 카드 조회/변환은 knowledge_cards/service.py의 CARD_SELECT, _to_card와 같은 모양으로 맞춘다
    evidence = CardEvidence(
        id=MOCK_ID,
        document_id=MOCK_ID,
        document_title="Mock 문서",
        original_filename="mock.md",
        location="Mock 위치",
        excerpt="Mock 근거 발췌문입니다.",
    )
    card = KnowledgeCard(
        id=MOCK_ID,
        title=f"{MOCK_LABEL} 테스트 지식카드",
        summary="실제 검색 결과가 아닌 테스트 카드입니다.",
        context="챗봇 API를 RAG 연결 전에 프론트와 연동하기 위한 Mock 데이터입니다.",
        problem=None,
        cause=None,
        approach="검색과 답변 생성을 Mock 함수로 분리해 두고 나중에 교체합니다.",
        rationale=None,
        outcome=None,
        lesson="Mock 응답은 실제 결과와 구분되도록 표시합니다.",
        technologies=[],
        keywords=[],
        status="active",
        quality_score=None,
        last_verified_at=None,
        verification_version=None,
        project_name=None,
        department=None,
        project_date=None,
        created_at=MOCK_TIMESTAMP,
        updated_at=MOCK_TIMESTAMP,
        evidences=[evidence],
    )
    return [
        ChatReference(
            id=evidence.id,
            type="knowledge-card",
            label=card.title,
            card=card,
            reason=f"{MOCK_LABEL} 실제 검색 결과가 아닌 테스트 근거입니다.",
            document=ChatSourceDocument(
                id=evidence.document_id,
                document_title=evidence.document_title,
                location=evidence.location,
                excerpt=evidence.excerpt,
            ),
        )
    ]


async def _generate_answer(
    message: str, history: list[ChatMessage], references: list[ChatReference]
) -> AsyncIterator[str]:
    # mock: 실제로는 references를 컨텍스트로 LLM을 스트리밍 호출해 토큰을 그대로 흘려보낸다
    for token in [
        f"{MOCK_LABEL} ",
        "실제 RAG 검색 없이 ",
        "만든 테스트 답변입니다. ",
        f"받은 질문: {message}",
    ]:
        yield token
        await _wait()


async def chat_stream(message: str, history: list[ChatMessage]) -> AsyncIterator[ServerSentEvent]:
    # token(여러 번) -> references -> completed 순서로 보낸다.
    # 스트림이 시작된 뒤엔 HTTP 상태코드를 바꿀 수 없어서, 실패는 error 이벤트로 알리고 끝낸다
    try:
        references = await _retrieve_references(message, history)
        async for token in _generate_answer(message, history, references):
            yield to_sse("token", ChatTokenEvent(content=token))
        yield to_sse("references", ChatReferencesEvent(items=references))
    except Exception:
        logger.exception("챗봇 답변 생성 실패")
        yield to_sse("error", ChatErrorEvent(message=CHAT_FAILED_MESSAGE))
        return
    yield to_sse("completed", ChatCompletedEvent())
