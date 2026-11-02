# 노드: 청크별 실행 기록과 이번 추출 대상 목록을 준비한다.

from langchain_core.runnables import RunnableConfig

from app.domains.documents.pipeline.chunk_execution import prepare_extraction as prepare
from app.domains.documents.pipeline.execution_budget import (
    GRAPH_RECURSION_LIMIT,
    check_execution_budget,
)


def prepare_extraction(state, config: RunnableConfig):
    estimate = check_execution_budget(
        state,
        limit=config.get("recursion_limit", GRAPH_RECURSION_LIMIT),
        used_steps=config.get("metadata", {}).get("langgraph_step", 0),
    )
    return {**prepare(state), "estimated_graph_steps": estimate}
