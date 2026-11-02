# 노드: 그래프 입력을 데이터 모델로 변환하고 새 작업의 실행 상태를 초기화한다.

import inspect

from app.domains.documents.pipeline.execution import initialize as initialize_state


def initialize(state):
    return initialize_state(state)


def make_initialize_node(node, gate_retry_budget=None):
    async def begin(state):
        from app.config import settings

        update = node(state)
        if inspect.isawaitable(update):
            update = await update
        budget = settings.VERIFY_MAX_RETRIES if gate_retry_budget is None else gate_retry_budget
        return {**update, "gate_retry_budget": budget}

    return begin
