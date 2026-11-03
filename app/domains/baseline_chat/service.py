# 기본 RAG 챗봇 로직: 검색 그래프(rewrite -> retrieve -> rerank -> expand)를 실행한 뒤
# generate 노드로 답변을 스트리밍하고 SSE 이벤트로 흘려보낸다.
# 이벤트 순서와 모양은 기존 챗봇과 같다: token(여러 번) -> references -> completed, 실패하면 error.
import asyncio
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi.sse import ServerSentEvent
from langgraph.graph.state import CompiledStateGraph

from app.core.sse import to_sse
from app.domains.baseline_chat import repository
from app.domains.baseline_chat.pipeline.graph import build_graph
from app.domains.baseline_chat.pipeline.nodes.generate import generate
from app.domains.baseline_chat.pipeline.state import ContextChunk
from app.domains.baseline_chat.schemas import BaselineReference, BaselineReferencesEvent
from app.domains.chat.schemas import (
    ChatCompletedEvent,
    ChatErrorEvent,
    ChatMessage,
    ChatTokenEvent,
)

logger = logging.getLogger(__name__)

CHAT_FAILED_MESSAGE = "답변을 생성하지 못했습니다."


@lru_cache
def get_graph() -> CompiledStateGraph:
    return build_graph()


def _to_references(contexts: list[ContextChunk], titles: dict[str, str]) -> list[BaselineReference]:
    return [
        BaselineReference(
            number=number,
            chunk_id=chunk["id"],
            document_id=chunk["document_id"],
            document_title=titles.get(chunk["document_id"], "제목 없음"),
            location=chunk["location"],
            excerpt=chunk["content"],
            score=chunk["score"],
        )
        for number, chunk in enumerate(contexts, start=1)
    ]


async def chat_stream(message: str, history: list[ChatMessage]) -> AsyncIterator[ServerSentEvent]:
    # 스트림이 시작된 뒤엔 HTTP 상태코드를 바꿀 수 없어서, 실패는 error 이벤트로 알리고 끝낸다
    try:
        state = await get_graph().ainvoke({"message": message, "history": history})
        contexts: list[ContextChunk] = state["contexts"]
        document_ids = list(dict.fromkeys(chunk["document_id"] for chunk in contexts))
        titles = await asyncio.to_thread(repository.get_document_titles, document_ids)

        async for token in generate(message, history, contexts, titles):
            yield to_sse("token", ChatTokenEvent(content=token))
        yield to_sse("references", BaselineReferencesEvent(items=_to_references(contexts, titles)))
    except Exception:
        logger.exception("기본 RAG 챗봇 답변 생성 실패")
        yield to_sse("error", ChatErrorEvent(message=CHAT_FAILED_MESSAGE))
        return
    yield to_sse("completed", ChatCompletedEvent())
