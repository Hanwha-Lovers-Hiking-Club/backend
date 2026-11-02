import asyncio
from collections import Counter
from copy import deepcopy
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.domains.documents.pipeline.document_lifecycle import (
    card_payload_hashes,
    document_scope,
    release_completed_checkpoints,
    save_payload_hash,
)
from app.domains.documents.pipeline.embeddings.models import EmbeddingError, EmbeddingSpec
from app.domains.documents.pipeline.execution_budget import (
    GRAPH_RECURSION_LIMIT,
    MAX_GATE_RETRIES,
    JobTooLargeError,
    check_execution_budget,
)
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.nodes.embed import make_embed_node
from tests.test_document_processing import Embedder, Model, Saver, graph, inputs, source


class FlakyStage(Model):
    def __init__(self, stage, failures):
        super().__init__()
        self.stage, self.failures, self.stage_calls = stage, failures, Counter()

    async def generate(self, **kwargs):
        payload = kwargs["payload"]
        if "extraction" in payload:
            stage, text = "draft", payload["extraction"]["context"]["value"]
        elif "claims" in payload:
            stage, text = "verify", payload["source_extraction"]["context"]["value"]
        else:
            return await super().generate(**kwargs)
        self.stage_calls[stage, text] += 1
        if (
            stage == self.stage
            and "FLAKY" in text
            and self.stage_calls[stage, text] <= self.failures
        ):
            raise GenerationError("LLM_TIMEOUT", "Synthetic temporary failure")
        return await super().generate(**kwargs)


@pytest.mark.parametrize("stage", ["draft", "verify"])
def test_transient_retry_is_per_experience_and_does_not_spend_gate_budget(tmp_path, stage):
    model = FlakyStage(stage, failures=1)
    item = source(tmp_path, 1, "Stable independent experience.\n\nFLAKY independent experience.")
    state = asyncio.run(graph(model).ainvoke(inputs([item])))
    calls = {
        text.strip(): count for (kind, text), count in model.stage_calls.items() if kind == stage
    }
    assert calls == {"Stable independent experience.": 1, "FLAKY independent experience.": 2}
    assert len(state["verified_drafts"]) == 2
    assert not state["verification_history"] and not state["processing_failures"]
    assert all(count == 1 for count in model.calls.values())


@pytest.mark.parametrize("stage", ["draft", "verify"])
def test_exhausted_transient_retry_is_distinct_from_semantic_uncertainty(tmp_path, stage):
    model = FlakyStage(stage, failures=99)
    item = source(tmp_path, 1, "FLAKY development experience.")
    data = inputs([item])
    data["processing_options"]["max_call_attempts"] = 2
    state = asyncio.run(graph(model).ainvoke(data))
    assert next(count for (kind, _), count in model.stage_calls.items() if kind == stage) == 2
    assert not state["verification_history"]
    if stage == "verify":
        issues = state["verification_results"][0].issues
        assert {item.code for item in issues} == {"VERIFIER_UNAVAILABLE"}
        assert state["document_progress"][str(UUID(int=1))].status == "needs_review"
    else:
        assert state["processing_failures"][0].stage == "draft_card"
        assert state["document_progress"][str(UUID(int=1))].status == "incomplete"


class ReviewOne(Model):
    async def generate(self, **kwargs):
        result = await super().generate(**kwargs)
        payload = kwargs["payload"]
        if "claims" in payload and "REVIEW" in payload["source_extraction"]["context"]["value"]:
            for item in result.assessments:
                if item.key == "draft.summary":
                    item.verdict = "uncertain"
        return result


def mixed_source(tmp_path):
    return source(tmp_path, 1, "Stable independent experience.\n\nREVIEW independent experience.")


def test_pass_cards_are_embedded_and_saved_while_review_material_is_preserved(tmp_path):
    saver, client = Saver(), Embedder()
    state = asyncio.run(
        graph(ReviewOne(), saver=saver, client=client).ainvoke(inputs([mixed_source(tmp_path)]))
    )
    assert len(saver.requests) == len(client.calls) == 1
    request = saver.requests[0]
    assert len(request.cards) == len(request.embeddings) == 1 and request.review_pending
    assert all("REVIEW" not in item.context for item in request.cards)
    assert len(request.card_payload_hashes) == 1
    progress = state["document_progress"][str(UUID(int=1))]
    assert progress.status == "needs_review" and len(progress.saved_card_ids) == 1
    assert progress.admitted_cards == progress.review_cards == 1
    assert len(state["drafts"]) == 2 and len(state["review_drafts"]) == 1
    assert len(state["chunk_progress"]) == len(state["chunks"]) == 2
    assert state["documents"] and state["parsed_documents"]
    assert state["review_drafts"][0].source_extraction.evidence_index


def test_review_without_saver_keeps_pass_vectors_and_all_evidence(tmp_path):
    state = asyncio.run(graph(ReviewOne()).ainvoke(inputs([mixed_source(tmp_path)])))
    assert len(state["embeddings"]) == 1
    progress = state["document_progress"][str(UUID(int=1))]
    assert progress.status == "needs_review" and not progress.saved_card_ids
    assert state["chunk_progress"] and len(state["drafts"]) == 2


def test_review_only_document_does_not_create_empty_partial_save(tmp_path):
    saver, client = Saver(), Embedder()
    item = source(tmp_path, 1, "REVIEW independent experience.")
    state = asyncio.run(graph(ReviewOne(), saver=saver, client=client).ainvoke(inputs([item])))
    assert not saver.requests and not client.calls
    assert state["document_progress"][str(UUID(int=1))].status == "needs_review"
    assert state["chunks"] and state["review_drafts"]


def test_partial_save_cannot_release_job_checkpoints(tmp_path):
    saver, checkpointer = Saver(), InMemorySaver()
    compiled = graph(ReviewOne(), saver=saver, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "partial-review"}}

    async def run():
        state = await compiled.ainvoke(inputs([mixed_source(tmp_path)]), config)
        with pytest.raises(ValueError):
            await release_completed_checkpoints(
                state, checkpointer, "partial-review", job_result_recorded=True
            )
        assert list(checkpointer.list(config))

    asyncio.run(run())


def test_saved_pass_hash_is_independent_of_review_and_skip_metadata(tmp_path):
    state = asyncio.run(graph(ReviewOne()).ainvoke(inputs([mixed_source(tmp_path)])))
    scope = document_scope(state, UUID(int=1))
    before = save_payload_hash(scope)
    per_card = card_payload_hashes(scope)
    changed = deepcopy(scope)
    changed["verification_results"][1].issues[0].reason = "Updated review explanation"
    changed["draft_skips"] = []
    changed["review_drafts"][0].title = "A revised review draft"
    assert save_payload_hash(changed) == before
    assert card_payload_hashes(changed) == per_card
    # 승인 카드가 늘면 집합 해시는 바뀌지만 기존 카드의 해시는 유지한다.
    accepted = changed["review_drafts"][0]
    changed["verified_drafts"].append(accepted)
    assert save_payload_hash(changed) != before
    assert all(card_payload_hashes(changed)[key] == value for key, value in per_card.items())


def test_embedding_service_retries_only_failed_batch_and_respects_retry_after(
    tmp_path, monkeypatch
):
    state = asyncio.run(
        graph(Model()).ainvoke(inputs([source(tmp_path, 1, "A complete experience.")]))
    )
    calls, sleeps = [], []
    client = Embedder()

    async def sleep(delay):
        sleeps.append(delay)

    base_embed = client.embed

    async def flaky_embed(texts):
        calls.append(tuple(texts))
        if len(calls) == 2:
            raise EmbeddingError("EMBEDDING_RATE_LIMITED", retryable=True, retry_after=2)
        return await base_embed(texts)

    monkeypatch.setattr(client, "embed", flaky_embed)
    monkeypatch.setattr("app.domains.documents.pipeline.call_retries.asyncio.sleep", sleep)
    result = asyncio.run(
        make_embed_node(
            client,
            spec=EmbeddingSpec(
                provider="test",
                model="test-model",
                dimensions=2,
            ),
            batch_size=2,
        )(state)
    )
    assert len(calls) == 3 and calls[1] == calls[2] and calls[0] != calls[1]
    assert sleeps == [2]
    assert len(result["embedding_usage"]) == 2 and len(result["embeddings"]) == 1


def test_graph_budget_rejects_oversized_work_before_model_calls(tmp_path):
    model = Model()
    item = source(
        tmp_path, 1, "\n\n".join(f"Independent development experience {i}." for i in range(60))
    )
    with pytest.raises(JobTooLargeError, match="JOB_TOO_LARGE") as error:
        asyncio.run(graph(model).ainvoke(inputs([item])))
    assert error.value.estimated_steps >= GRAPH_RECURSION_LIMIT
    assert not model.calls


def test_graph_budget_respects_smaller_invocation_limit(tmp_path):
    model = Model()
    item = source(tmp_path, 1, "One complete experience.")
    with pytest.raises(JobTooLargeError) as error:
        asyncio.run(graph(model).ainvoke(inputs([item]), {"recursion_limit": 60}))
    assert error.value.limit == 60 and not model.calls


def test_exhausted_embedding_retry_does_not_repeat_successful_batch(tmp_path):
    state = asyncio.run(
        graph(Model()).ainvoke(inputs([source(tmp_path, 1, "A complete experience.")]))
    )
    before, calls = deepcopy(state), []
    client = Embedder()
    base_embed = client.embed

    async def flaky(texts):
        calls.append(tuple(texts))
        if len(calls) > 1:
            raise EmbeddingError("EMBEDDING_UNAVAILABLE", retryable=True)
        return await base_embed(texts)

    client.embed = flaky
    with pytest.raises(EmbeddingError):
        asyncio.run(
            make_embed_node(
                client,
                spec=EmbeddingSpec(
                    provider="test",
                    model="test-model",
                    dimensions=2,
                ),
                batch_size=2,
            )(state)
        )
    assert len(calls) == 4 and calls[0] != calls[1] and calls[1] == calls[2] == calls[3]
    assert state == before


def test_budget_includes_already_scheduled_but_unexecuted_repairs(tmp_path):
    from app.domains.documents.pipeline.devtools.demo import get_demo_graph
    from app.domains.documents.pipeline.verification.models import RetryWork

    state = asyncio.run(get_demo_graph().ainvoke({}))
    request = state["verification_history"][0]
    state["verification_history"] = [request] * MAX_GATE_RETRIES
    state["pending_retries"] = []
    estimate = check_execution_budget(state)
    state["pending_retries"] = [RetryWork(request=request, draft=state["drafts"][0])]
    with pytest.raises(JobTooLargeError):
        check_execution_budget(state, limit=estimate + 1)
