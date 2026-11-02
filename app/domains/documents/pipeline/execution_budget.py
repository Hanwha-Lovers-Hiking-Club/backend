# 청킹 후 유료 호출 전에 그래프 단계 수의 보수적 상한을 확인한다.

GRAPH_RECURSION_LIMIT = 1000
# VerificationOptions가 허용하는 최대 내용 재작성 예산까지 예약한다.
MAX_GATE_RETRIES = 5


class JobTooLargeError(ValueError):
    code = "JOB_TOO_LARGE"

    def __init__(self, estimated_steps, limit):
        self.estimated_steps, self.limit = estimated_steps, limit
        super().__init__(f"JOB_TOO_LARGE: estimated graph steps {estimated_steps} exceed {limit}")


def check_execution_budget(state, *, limit=GRAPH_RECURSION_LIMIT, used_steps=0):
    chunks = {item.chunk_id for item in [*state.get("all_chunks", []), *state["chunks"]]}
    attempts = state["processing_options"].max_chunk_attempts
    current_calls = max(1, len({item.chunk_id for item in state["chunks"]}) * attempts)
    all_calls = max(1, len(chunks) * attempts)
    documents = len(state.get("all_sources", state.get("sources", [])))
    remaining_repairs = max(0, MAX_GATE_RETRIES - len(state.get("verification_history", [])))
    # 이미 history에 기록됐지만 아직 실행하지 않은 큐도 앞으로 필요한 단계다.
    remaining_repairs += len(state.get("pending_retries", []))
    # 고정 단계와 문서별 embed/save/complete, 각 재작성의 prepare·상위 노드·후속 단계를 포함한다.
    estimated = used_steps + 16 + current_calls + 4 * max(1, documents)
    estimated += remaining_repairs * (10 + all_calls)
    if estimated >= limit:
        raise JobTooLargeError(estimated, limit)
    return estimated
