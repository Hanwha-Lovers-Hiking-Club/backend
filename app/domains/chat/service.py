# 챗봇 로직: 질문에 맞는 지식카드를 찾고(검색) 답변을 만들어(생성) SSE 이벤트로 흘려보낸다.
# 지금 검색/생성은 mock이다.
# RAG가 준비되면 _retrieve_references, _generate_answer 두 함수만 교체한다.
import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi.sse import ServerSentEvent

from app.core.schemas import CamelModel
from app.domains.chat.schemas import (
    ChatCompletedEvent,
    ChatErrorEvent,
    ChatMessage,
    ChatReference,
    ChatReferencesEvent,
    ChatTokenEvent,
)

logger = logging.getLogger(__name__)

CHAT_FAILED_MESSAGE = "답변을 생성하지 못했습니다."
# mock 답변을 토큰 단위로 나눠 보낼 때 토큰 사이 간격(초). 스트리밍처럼 보이게 하는 용도
TOKEN_INTERVAL = 0.05

# mock 표시. 실제 답변/근거로 오인하지 않도록 답변 첫 토큰과 근거 reason에 붙인다
MOCK_LABEL = "[Mock]"
# DB에 없는 nil UUID라 실제 카드/문서와 겹치지 않는다
MOCK_ID = UUID(int=0)


async def _wait() -> None:
    await asyncio.sleep(TOKEN_INTERVAL)


async def _retrieve_references(message: str, history: list[ChatMessage]) -> list[ChatReference]:
    # mock: 실제로는 질문을 임베딩해 active 지식카드를 검색하고 근거(card_documents)를 붙인다
    return [
        ChatReference(
            card_id=MOCK_ID,
            reason=f"{MOCK_LABEL} 실제 검색 결과가 아닌 테스트 근거입니다.",
            document_id=MOCK_ID,
            location="Mock 위치",
            excerpt="Mock 근거 발췌문입니다.",
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


def _sse(event: str, payload: CamelModel) -> ServerSentEvent:
    # FastAPI SSE는 모델을 alias 없이(snake_case) 직렬화해서, camelCase JSON 문자열로 직접 넘긴다
    return ServerSentEvent(event=event, raw_data=payload.model_dump_json(by_alias=True))


async def chat_stream(message: str, history: list[ChatMessage]) -> AsyncIterator[ServerSentEvent]:
    # token(여러 번) -> references -> completed 순서로 보낸다.
    # 스트림이 시작된 뒤엔 HTTP 상태코드를 바꿀 수 없어서, 실패는 error 이벤트로 알리고 끝낸다
    try:
        references = await _retrieve_references(message, history)
        async for token in _generate_answer(message, history, references):
            yield _sse("token", ChatTokenEvent(content=token))
        yield _sse("references", ChatReferencesEvent(items=references))
    except Exception:
        logger.exception("챗봇 답변 생성 실패")
        yield _sse("error", ChatErrorEvent(message=CHAT_FAILED_MESSAGE))
        return
    yield _sse("completed", ChatCompletedEvent())
