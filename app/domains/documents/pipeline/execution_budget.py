# 청킹 후 유료 호출 전에 그래프 단계 수의 보수적 상한을 확인한다.

from collections import Counter

from app.domains.documents.pipeline.retry_scope import retry_chunks, retry_document_ids

GRAPH_RECURSION_LIMIT = 20000
# 스키마의 상한이다. 실행 예산은 초기화 시 확정한 gate_retry_budget을 사용한다.
MAX_GATE_RETRIES = 5


class JobTooLargeError(ValueError):
    code = "JOB_TOO_LARGE"

    def __init__(self, estimated_steps, limit):
        self.estimated_steps, self.limit = estimated_steps, limit
        super().__init__(f"{self.code}: estimated graph steps {estimated_steps} exceed {limit}")


class GraphBudgetConfigurationError(JobTooLargeError):
    code = "GRAPH_STEP_BUDGET_TOO_SMALL"


class JobStepBudgetExceededError(JobTooLargeError):
    code = "JOB_STEP_BUDGET_EXCEEDED"


def known_chunks(state):
    old = state.get("all_chunks", [])
    active = state.get("active_retry")
    if active is not None and active.request.target_node != "extract":
        replaced = {item.document_id for item in state.get("documents", [])}
        old = [item for item in old if item.document_id not in replaced]
    return list({item.chunk_id: item for item in [*old, *state["chunks"]]}.values())


def retry_steps(work, chunks, attempts):
    target = work.request.target_node
    if target == "draft_card":
        return 4
    calls = max(1, len(retry_chunks(work, chunks)) * attempts)
    overhead = {"extract": 7, "chunk": 8, "normalize": 9, "parse": 10}
    return overhead[target] + calls


def estimate_execution_steps(state, *, used_steps=0, allowed_document_ids=None):
    chunks = known_chunks(state)
    allowed = (
        {item.document_id for item in chunks}
        if allowed_document_ids is None
        else allowed_document_ids
    )
    attempts = state["processing_options"].max_chunk_attempts
    selected = [item for item in chunks if item.document_id in allowed]
    current_calls = max(
        1,
        len({item.chunk_id for item in state["chunks"] if item.document_id in allowed}) * attempts,
    )
    pending = state.get("pending_retries", [])
    queued_steps = sum(
        retry_steps(work, selected, attempts) if retry_document_ids(work) <= allowed else 2
        for work in pending
    )
    potential = {item.document_id for item in state["chunks"]}
    potential.update(document_id for work in pending for document_id in retry_document_ids(work))
    sizes = Counter(item.document_id for item in selected if item.document_id in potential)
    free_retries = max(
        0,
        state.get("gate_retry_budget", MAX_GATE_RETRIES)
        - len(state.get("verification_history", [])),
    )
    # 아직 원인이 미정인 재작성은 가장 큰 문서 전체 Parse 경로까지 예약한다.
    unknown_steps = (10 + max(sizes.values()) * attempts) if sizes else 0
    documents = len(state.get("all_sources", state.get("sources", [])))
    return (
        used_steps
        + 12
        + current_calls
        + 3 * max(1, documents)
        + queued_steps
        + free_retries * unknown_steps
    )


def check_execution_budget(state, *, limit=GRAPH_RECURSION_LIMIT, used_steps=0):
    estimated = estimate_execution_steps(state, used_steps=used_steps)
    if estimated >= limit:
        raise JobTooLargeError(estimated, limit)
    return estimated


def admit_extraction(state, *, limit=GRAPH_RECURSION_LIMIT, used_steps=0):
    from app.domains.documents.pipeline.processing import ProcessingFailure

    failures = list(state.get("processing_failures", []))
    blocked = {item.document_id for item in failures if item.stage == "budget"}
    chunks = known_chunks(state)
    sizes = Counter(item.document_id for item in state["chunks"] if item.document_id not in blocked)
    admitted = {item.document_id for item in chunks} - set(sizes) - blocked
    baseline = estimate_execution_steps(state, used_steps=used_steps, allowed_document_ids=set())
    if baseline >= limit:
        # 실행 후 결과를 기록할 단계조차 없는 설정은 문서 오류와 구분한다.
        raise GraphBudgetConfigurationError(baseline, limit)
    # 예산이 모자라면 작은 문서를 먼저 수용하며 동률은 원래 입력 순서를 유지한다.
    for document_id in sorted(sizes, key=sizes.get):
        individual = estimate_execution_steps(
            state, used_steps=used_steps, allowed_document_ids={document_id}
        )
        combined = estimate_execution_steps(
            state, used_steps=used_steps, allowed_document_ids=admitted | {document_id}
        )
        if individual >= limit or combined >= limit:
            failures.append(
                ProcessingFailure(
                    document_id=document_id,
                    stage="budget",
                    code="JOB_TOO_LARGE" if individual >= limit else "JOB_STEP_BUDGET_EXCEEDED",
                )
            )
        else:
            admitted.add(document_id)
    estimated = estimate_execution_steps(
        state, used_steps=used_steps, allowed_document_ids=admitted
    )
    if estimated >= limit:
        raise JobStepBudgetExceededError(estimated, limit)
    return {
        "chunks": [item for item in state["chunks"] if item.document_id in admitted],
        "processing_failures": failures,
        "estimated_graph_steps": estimated,
    }
