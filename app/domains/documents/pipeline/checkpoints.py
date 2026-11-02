# 제품 문서 작업의 SQLite 체크포인트를 관리하고 보류 카드·근거를 실행 종료 후에도 보존한다.

import asyncio
from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.domains.documents.pipeline.document_lifecycle import release_completed_checkpoints
from app.domains.documents.pipeline.state import IngestionState


@asynccontextmanager
async def job_checkpointer():
    from app.config import settings

    path = settings.DOCUMENT_CHECKPOINT_PATH.resolve()
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        yield saver


async def stream_document_job(job_id, sources, *, graph_factory):
    # 영속 job ID를 thread ID로 사용한다. SSE 구독은 이 함수를 호출하지 않는다.
    config = {"configurable": {"thread_id": str(job_id)}}
    async with job_checkpointer() as saver:
        graph = graph_factory(checkpointer=saver)
        async for update in graph.astream(
            {"sources": sources},
            config,
            stream_mode="updates",
            durability="sync",
        ):
            yield update


async def load_job_state(job_id):
    # 검토/진단용 내부 진입점이다. 임시 원본 경로는 만료되므로 자동 작업 재실행에는 사용하지 않는다.
    config = {"configurable": {"thread_id": str(job_id)}}
    async with job_checkpointer() as saver:
        saved = await saver.aget_tuple(config)
    if saved is None:
        return None
    return {
        key: value
        for key, value in saved.checkpoint["channel_values"].items()
        if key in IngestionState.__annotations__
    }


async def release_job_checkpoints(job_id):
    # 호출자는 DB의 최종 job 결과 기록에 성공한 경우에만 호출한다.
    async with job_checkpointer() as saver:
        saved = await saver.aget_tuple({"configurable": {"thread_id": str(job_id)}})
        if saved is not None:
            await release_completed_checkpoints(
                saved.checkpoint["channel_values"],
                saver,
                str(job_id),
                job_result_recorded=True,
            )
