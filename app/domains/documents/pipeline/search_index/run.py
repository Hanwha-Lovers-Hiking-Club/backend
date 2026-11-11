# 원문 검색 인덱싱 경로를 순서대로 실행한다: parse -> chunk -> embed_chunks -> save_chunks.
# dev 그래프에서 이 네 노드가 하던 실패 정책을 그대로 따른다.
#   - parse: 읽지 못한 문서는 건너뛰고, 모두 못 읽었을 때만 error(이 경로만 끝남)
#   - embed_chunks / save_chunks: 실패하면 warnings만 남기고 끝난다
#   - 노드 안에서 처리하지 못한 예외는 error로 바꿔 이 경로만 끝낸다(작업은 실패시키지 않음)
import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.logging import summarize_error
from app.domains.documents.pipeline.observe import NodeRun
from app.domains.documents.pipeline.search_index.chunk import chunk
from app.domains.documents.pipeline.search_index.embed_chunks import embed_chunks
from app.domains.documents.pipeline.search_index.parse import parse
from app.domains.documents.pipeline.search_index.save_chunks import save_chunks
from app.domains.documents.pipeline.search_index.state import DocumentInput, IndexState

logger = logging.getLogger(__name__)

# (진행 이벤트에 기록할 단계 이름, 노드)
INDEX_STEPS = (
    ("index_parse", parse),
    ("index_chunk", chunk),
    ("embed_chunks", embed_chunks),
    ("save_chunks", save_chunks),
)


async def index_documents(
    job_id: UUID,
    documents: list[DocumentInput],
    on_step: Callable[[str], Awaitable[None]] | None = None,
) -> IndexState:
    state: IndexState = {"job_id": job_id, "documents": documents, "warnings": []}
    for name, node in INDEX_STEPS:
        # dev 그래프의 _safe처럼 단계마다 시작/완료 로그와 job 요약을 남긴다(observe.NodeRun)
        run = NodeRun(name, state, is_last=name == INDEX_STEPS[-1][0])
        try:
            try:
                if inspect.iscoroutinefunction(node):
                    result = await node(state)
                else:
                    # 동기 노드는 이벤트 루프를 막지 않도록 스레드에서 돈다
                    result = await asyncio.to_thread(node, state)
                raised = False
            except Exception as e:
                logger.exception("%s 단계에서 예기치 못한 오류", name)
                result = {"error": f"{name} 단계에서 예기치 못한 오류: {summarize_error(e)}"}
                raised = True
            run.log_done(result or {}, raised)
        finally:
            run.close()
        state.update(result or {})
        if on_step is not None:
            await on_step(name)
        if state.get("error"):
            logger.warning("원문 검색 인덱싱을 건너뜀: %s", state["error"])
            break
    return state
