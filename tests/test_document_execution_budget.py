import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domains.documents.pipeline.document import ChunkOptions, DocumentSource
from app.domains.documents.pipeline.execution import initialize, prepare_retry
from app.domains.documents.pipeline.execution_budget import (
    GRAPH_RECURSION_LIMIT,
    GraphBudgetConfigurationError,
    admit_extraction,
    estimate_execution_steps,
    retry_steps,
)
from app.domains.documents.pipeline.nodes.chunk import chunk
from app.domains.documents.pipeline.nodes.normalize import normalize
from app.domains.documents.pipeline.nodes.parse import parse
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.processing import ProcessingOptions
from app.domains.documents.pipeline.verification.models import (
    RetryRequest,
    RetryWork,
    VerificationIssue,
    VerificationOptions,
)
from tests.test_document_processing import Model, Saver, graph, inputs, source


def estimated_state(counts, *, retries=2, attempts=3):
    chunks = [
        SimpleNamespace(document_id=UUID(int=index), chunk_id=f"{index}:{offset}")
        for index, count in enumerate(counts, 1)
        for offset in range(count)
    ]
    return {
        "chunks": chunks,
        "all_chunks": [],
        "sources": [None] * len(counts),
        "gate_retry_budget": retries,
        "processing_options": ProcessingOptions(max_chunk_attempts=attempts),
        "verification_history": [],
        "pending_retries": [],
    }


def test_uses_selected_gate_budget_and_largest_document_instead_of_entire_job():
    low = estimate_execution_steps(estimated_state([60, 60], retries=0))
    default = estimate_execution_steps(estimated_state([60, 60], retries=2))
    maximum = estimate_execution_steps(estimated_state([60, 60], retries=5))
    assert low < default < maximum
    assert default < 1000
    # 총 청크 수가 같아도 한 문서 전체를 재처리해야 하는 경우의 상한은 더 크다.
    assert default < estimate_execution_steps(estimated_state([120], retries=2))


def test_individual_document_failure_does_not_reject_normal_document():
    state = estimated_state([3000, 1])
    update = admit_extraction(state)
    assert {item.document_id for item in update["chunks"]} == {UUID(int=2)}
    assert [(item.document_id, item.code) for item in update["processing_failures"]] == [
        (UUID(int=1), "JOB_TOO_LARGE")
    ]
    assert update["estimated_graph_steps"] < GRAPH_RECURSION_LIMIT


def test_job_capacity_exhaustion_is_not_reported_as_intrinsically_large_document():
    state = estimated_state([100, 100, 1])
    update = admit_extraction(state, limit=1000)
    assert len(update["processing_failures"]) == 1
    assert update["processing_failures"][0].code == "JOB_STEP_BUDGET_EXCEEDED"
    assert UUID(int=3) in {item.document_id for item in update["chunks"]}
    assert update["estimated_graph_steps"] < 1000


def test_invalid_execution_limit_is_distinct_from_document_size():
    with pytest.raises(GraphBudgetConfigurationError) as error:
        admit_extraction(estimated_state([1]), limit=3)
    assert error.value.code == "GRAPH_STEP_BUDGET_TOO_SMALL"


def test_actual_graph_saves_small_document_after_budget_failure(tmp_path):
    model, saver = Model(), Saver()
    big = source(
        tmp_path, 1, "\n\n".join(f"Experience number {i} is independent." for i in range(40))
    )
    small = source(tmp_path, 2, "A stable experience.")
    state = asyncio.run(
        graph(model, saver=saver).ainvoke(inputs([big, small]), {"recursion_limit": 100})
    )
    assert state["document_progress"][str(UUID(int=1))].status == "failed"
    assert state["document_progress"][str(UUID(int=2))].status == "completed"
    assert len(model.calls) == len(saver.requests) == 1
    assert saver.requests[0].document.document_id == UUID(int=2)


def test_ten_files_ten_megabytes_parse_chunk_and_fit_default_budget(tmp_path):
    # 업로드 한도 크기의 실제 파일을 실제 파서로 처리한다. LLM/API는 호출하지 않는다.
    part = ("A" * 5900 + "\n\n").encode()
    size = 1024 * 1024
    sources = []
    for index in range(1, 11):
        path = tmp_path / f"source-{index}.txt"
        path.write_bytes((part * (size // len(part) + 1))[:size])
        sources.append(
            DocumentSource(document_id=UUID(int=index), original_filename=path.name, path=path)
        )
    state = initialize({"sources": sources, "chunk_options": ChunkOptions(target_chars=6000)})
    state["gate_retry_budget"] = 2
    state.update(asyncio.run(parse(state)))
    state.update(normalize(state))
    state.update(chunk(state))
    assert len(state["chunks"]) > 1000
    admitted = admit_extraction(state)
    assert not admitted["processing_failures"]
    assert len(admitted["chunks"]) == len(state["chunks"])
    assert admitted["estimated_graph_steps"] < GRAPH_RECURSION_LIMIT


def test_runtime_verify_configuration_is_snapshotted_for_budget(tmp_path, monkeypatch):
    from app.config import settings
    from app.domains.documents.pipeline.devtools.demo import skip_demo_embedding
    from app.domains.documents.pipeline.graph import build_graph
    from app.domains.documents.pipeline.nodes.extract import make_extract_node

    monkeypatch.setattr(settings, "VERIFY_MAX_RETRIES", 0)
    compiled = build_graph(extract_node=make_extract_node(Model()), embed_node=skip_demo_embedding)
    # 빈 입력으로 유료 호출 없이 초기화 시점의 예산 스냅샷만 확인한다.
    state = asyncio.run(compiled.ainvoke({"sources": []}))
    assert state["gate_retry_budget"] == 0
    node = make_verify_node(options=VerificationOptions(max_retries=1), generator=Model())
    compiled = build_graph(
        verify_node=node,
        extract_node=make_extract_node(Model()),
        embed_node=skip_demo_embedding,
    )
    assert asyncio.run(compiled.ainvoke({"sources": []}))["gate_retry_budget"] == 1


@pytest.mark.parametrize("target", ["draft_card", "extract", "chunk", "normalize", "parse"])
def test_known_retry_budget_matches_actual_execution_scope(tmp_path, target):
    item = source(
        tmp_path,
        1,
        "First independent development experience.\n\n"
        "Second independent development experience.\n\nThird independent development experience.",
    )
    state = asyncio.run(graph(Model()).ainvoke(inputs([item])))
    request = RetryRequest(
        experience_id=state["drafts"][0].experience_id,
        target_node=target,
        input_fingerprint="test-scope",
        issues=[
            VerificationIssue(
                check="retry",
                code="TEST_REPAIR",
                field="draft",
                reason="Synthetic scope test",
                action="Repair this experience",
                disposition="retry",
                retry_node=target,
            )
        ],
    )
    work = RetryWork(request=request, draft=state["drafts"][0])
    state["pending_retries"] = [work]
    prepared = prepare_retry(state)
    if target == "draft_card":
        assert retry_steps(work, state["all_chunks"], 3) == 4
    elif target == "extract":
        assert len(prepared["chunks"]) == 1 < len(state["all_chunks"])
        assert retry_steps(work, state["all_chunks"], 3) == 7 + len(prepared["chunks"]) * 3
    else:
        assert retry_steps(work, state["all_chunks"], 3) >= 8 + len(prepared["chunks"]) * 3
