import asyncio
from collections import Counter
from copy import deepcopy
from uuid import UUID, uuid5

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.domains.documents.pipeline.chunk_execution import prepare_extraction
from app.domains.documents.pipeline.document_lifecycle import (
    DocumentSaveError,
    release_completed_checkpoints,
)
from app.domains.documents.pipeline.embeddings.models import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingSpec,
    EmbeddingUsage,
)
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.embed import make_embed_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.processing import SaveReceipt
from app.domains.documents.pipeline.verification.models import VerificationOptions


class Model:
    def __init__(self, *, failures=0, code="LLM_API_ERROR", status=None):
        self.calls = Counter()
        self.failures, self.code, self.status = failures, code, status

    async def generate(self, *, instructions, payload, output_type):
        if "blocks" in payload:
            text = " ".join(item["text"] for item in payload["blocks"])
            key = payload["chunk_id"]
            self.calls[key] += 1
            if "FAIL" in text and self.calls[key] <= self.failures:
                error = GenerationError(self.code, "Synthetic failure")
                if self.status:
                    cause = RuntimeError("Synthetic HTTP failure")
                    cause.status_code = self.status
                    raise error from cause
                raise error
            ref = payload["blocks"][0]["evidence_id"]
            fact = {"value": text, "status": "confirmed", "evidence_refs": [ref]}
            return output_type.model_validate(
                {
                    "experiences": [
                        {
                            name: fact
                            for name in ("context", "problem", "approach", "outcome", "lesson")
                        }
                    ]
                }
            )
        if "extraction" in payload:
            text = payload["extraction"]["context"]["value"]
            return output_type.model_validate(
                {
                    "draft": {
                        "title": "An observed incident",
                        "summary": text,
                        "lesson": text,
                        "keywords": [],
                    },
                    "skip_reason": None,
                }
            )
        return output_type.model_validate(
            {
                "assessments": [
                    {
                        "key": item["key"],
                        "verdict": "supported",
                        "evidence_refs": item["evidence_refs"],
                        "reason": "Synthetic supported claim",
                        "failure_kind": None,
                    }
                    for item in payload["claims"]
                ]
            }
        )


class Embedder:
    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        return EmbeddingBatch(
            model="test-model",
            dimensions=2,
            vectors=[[1.0, 2.0] for _ in texts],
            usage=EmbeddingUsage(prompt_tokens=1, total_tokens=1),
        )


class Saver:
    def __init__(self, *, fail=None, mismatch=None):
        self.requests, self.fail, self.mismatch = [], fail, mismatch

    async def __call__(self, request):
        self.requests.append(request.model_copy(deep=True))
        if request.document.document_id == self.fail:
            raise DocumentSaveError("Synthetic interrupted transaction")
        return SaveReceipt(
            document_id=request.document.document_id,
            payload_hash=self.mismatch or request.payload_hash,
            committed=True,
            result_recorded=True,
            card_ids=[
                uuid5(request.document.document_id, str(i)) for i, _ in enumerate(request.cards)
            ],
        )


def source(tmp_path, number, text):
    path = tmp_path / f"document-{number}.txt"
    path.write_text(text, encoding="utf-8")
    return {"document_id": str(UUID(int=number)), "original_filename": path.name, "path": path}


def graph(model, *, saver=None, client=None, **kwargs):
    return build_graph(
        extract_node=make_extract_node(model),
        draft_node=make_draft_node(model),
        verify_node=make_verify_node(options=VerificationOptions(), generator=model),
        embed_node=make_embed_node(
            client or Embedder(),
            spec=EmbeddingSpec(
                provider="test",
                model="test-model",
                dimensions=2,
            ),
        ),
        document_saver=saver,
        **kwargs,
    )


def inputs(sources, *, attempts=3):
    return {
        "sources": sources,
        "chunk_options": {"target_chars": 30},
        "processing_options": {"max_chunk_attempts": attempts, "retry_delay_seconds": 0},
    }


def test_no_experiences_completes_with_zero_cards_and_one_receipt(tmp_path):
    class NoExperience(Model):
        async def generate(self, *, instructions, payload, output_type):
            if "blocks" in payload:
                return output_type.model_validate({"experiences": []})
            return await super().generate(
                instructions=instructions, payload=payload, output_type=output_type
            )

    saver = Saver()
    item = source(tmp_path, 1, "A valid document with no reusable incident.")
    state = asyncio.run(graph(NoExperience(), saver=saver).ainvoke(inputs([item])))
    progress = state["document_progress"][str(UUID(int=1))]
    assert progress.status == "completed"
    assert progress.admitted_cards == 0 and progress.saved_card_ids == []
    assert len(saver.requests) == 1
    assert saver.requests[0].cards == [] and saver.requests[0].embeddings == []


def test_only_failed_chunk_is_retried_and_successes_are_reused(tmp_path):
    model = Model(failures=1)
    item = source(
        tmp_path, 1, "First independent incident was solved.\n\nFAIL second incident was solved."
    )
    state = asyncio.run(graph(model).ainvoke(inputs([item])))
    assert sorted(model.calls.values()) == [1, 2]
    assert len(state["verified_drafts"]) == 2
    assert not state["processing_failures"]
    assert {item.status for item in state["chunk_progress"].values()} == {"succeeded"}
    assert state["document_progress"][str(UUID(int=1))].status == "ready_to_save"


@pytest.mark.parametrize(
    "code,status,expected_attempts",
    [
        ("LLM_API_ERROR", 429, 3),
        ("LLM_API_ERROR", 503, 3),
        ("LLM_API_ERROR", 401, 1),
        ("LLM_API_ERROR", 400, 1),
        ("INPUT_TOO_LARGE", None, 1),
        ("UNKNOWN_EVIDENCE", None, 1),
    ],
)
def test_retry_budget_and_permanent_failure_classification(
    tmp_path, code, status, expected_attempts
):
    model = Model(failures=99, code=code, status=status)
    first = source(tmp_path, 1, "FAIL this source cannot be extracted.")
    other = source(tmp_path, 2, "An independent successful incident.")
    state = asyncio.run(graph(model).ainvoke(inputs([first, other])))
    failed = next(item for item in state["chunk_progress"].values() if item.status == "failed")
    assert failed.attempts == expected_attempts
    assert len(state["processing_failures"]) == 1
    assert state["document_progress"][str(UUID(int=1))].status == "incomplete"
    assert state["document_progress"][str(UUID(int=2))].status == "ready_to_save"
    assert len(state["embeddings"]) == 1


def test_failed_chunk_prevents_partial_document_publication(tmp_path):
    model = Model(failures=99)
    item = source(
        tmp_path, 1, "A successful chunk with sufficient context.\n\nFAIL unresolved other chunk."
    )
    saver = Saver()
    state = asyncio.run(graph(model, saver=saver).ainvoke(inputs([item], attempts=2)))
    assert not state["verified_drafts"] and not state["embeddings"] and not saver.requests
    assert len(state["drafts"]) == 1
    assert state["document_progress"][str(UUID(int=1))].successful_chunks == 1
    assert state["document_progress"][str(UUID(int=1))].failed_chunks == 1


def test_corrupt_document_does_not_discard_other_document(tmp_path):
    bad = source(tmp_path, 1, "")
    good = source(tmp_path, 2, "A complete independent development experience.")
    state = asyncio.run(graph(Model()).ainvoke(inputs([bad, good])))
    assert state["processing_failures"][0].code == "EMPTY_FILE"
    assert state["document_progress"][str(UUID(int=1))].status == "failed"
    assert len(state["verified_drafts"]) == len(state["embeddings"]) == 1


def test_checkpoint_resume_preserves_each_successful_chunk(tmp_path):
    model = Model(failures=1)
    compiled = graph(model, checkpointer=InMemorySaver(), interrupt_before=["extract"])
    item = source(
        tmp_path, 1, "First independent incident was solved.\n\nFAIL second incident was solved."
    )
    config = {"configurable": {"thread_id": "chunk-resume"}}

    async def run():
        await compiled.ainvoke(inputs([item]), config)
        await compiled.ainvoke(None, config)
        state = compiled.get_state(config)
        assert len(model.calls) == 1
        assert (
            sum(item.status == "succeeded" for item in state.values["chunk_progress"].values()) == 1
        )
        await compiled.ainvoke(None, config)
        assert compiled.get_state(config).next == ("extract",)
        return await compiled.ainvoke(None, config)

    state = asyncio.run(run())
    assert sorted(model.calls.values()) == [1, 2]
    assert len(state["verified_drafts"]) == 2


def test_no_saver_keeps_temporary_results_until_real_save(tmp_path):
    item = source(tmp_path, 1, "One complete development incident.")
    state = asyncio.run(graph(Model()).ainvoke(inputs([item])))
    assert state["chunk_progress"] and state["chunks"] and state["documents"]
    assert state["embeddings"] and state["verified_drafts"]
    assert state["document_progress"][str(UUID(int=1))].status == "ready_to_save"


def test_confirmed_save_cleans_only_completed_document(tmp_path):
    good = source(tmp_path, 1, "One complete development incident.")
    failed = source(tmp_path, 2, "Another complete development incident.")
    saver = Saver(fail=UUID(int=2))
    state = asyncio.run(graph(Model(), saver=saver).ainvoke(inputs([good, failed])))
    assert state["document_progress"][str(UUID(int=1))].status == "completed"
    assert state["document_progress"][str(UUID(int=2))].status == "incomplete"
    for name in (
        "parsed_documents",
        "all_parsed_documents",
        "documents",
        "all_documents",
        "chunks",
        "all_chunks",
    ):
        assert {item.document_id for item in state[name]} == {UUID(int=2)}
    assert {item.document_id for item in state["chunk_progress"].values()} == {UUID(int=2)}
    assert len(state["drafts"]) == len(state["embeddings"]) == 1
    assert good["path"].exists() and failed["path"].exists()
    assert len(saver.requests) == 2


def test_invalid_save_acknowledgement_keeps_resume_material(tmp_path):
    item = source(tmp_path, 1, "One complete development incident.")
    state = asyncio.run(
        graph(Model(), saver=Saver(mismatch="wrong-request")).ainvoke(inputs([item]))
    )
    assert state["document_progress"][str(UUID(int=1))].status == "incomplete"
    assert state["processing_failures"][0].code == "SAVE_NOT_CONFIRMED"
    assert state["chunk_progress"] and state["drafts"] and state["embeddings"]


def test_embedding_failure_is_isolated_to_its_document(tmp_path):
    async def embed(state):
        if state["sources"][0].document_id == UUID(int=1):
            raise EmbeddingError("EMBEDDING_INPUT_TOO_LARGE")
        return await make_embed_node(
            Embedder(),
            spec=EmbeddingSpec(
                provider="test",
                model="test-model",
                dimensions=2,
            ),
        )(state)

    model = Model()
    compiled = build_graph(
        extract_node=make_extract_node(model),
        draft_node=make_draft_node(model),
        verify_node=make_verify_node(options=VerificationOptions(), generator=model),
        embed_node=embed,
    )
    items = [source(tmp_path, i, f"Complete development incident {i}.") for i in (1, 2)]
    state = asyncio.run(compiled.ainvoke(inputs(items)))
    assert len(state["embeddings"]) == 1
    assert state["document_progress"][str(UUID(int=1))].status == "incomplete"
    assert state["document_progress"][str(UUID(int=2))].status == "ready_to_save"


def test_complete_thread_cleanup_requires_all_documents_and_durable_job_summary(tmp_path):
    saver, checkpointer = Saver(), InMemorySaver()
    compiled = graph(Model(), saver=saver, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "cleanup"}}
    item = source(tmp_path, 1, "One complete development incident.")

    async def run():
        state = await compiled.ainvoke(inputs([item]), config)
        assert state["document_progress"][str(UUID(int=1))].status == "completed"
        assert not state["chunk_progress"] and not state["drafts"] and not state["embeddings"]
        assert list(checkpointer.list(config))
        with pytest.raises(ValueError):
            await release_completed_checkpoints(state, checkpointer, "cleanup")
        await release_completed_checkpoints(
            state, checkpointer, "cleanup", job_result_recorded=True
        )
        assert not list(checkpointer.list(config))

    asyncio.run(run())


def test_changed_source_or_feedback_does_not_reuse_stale_chunk_result(tmp_path):
    item = source(tmp_path, 1, "One complete development incident.")
    state = asyncio.run(graph(Model()).ainvoke(inputs([item])))
    before = deepcopy(state)
    update = prepare_extraction(state)
    assert len(update["chunk_progress"]) == len(state["chunk_progress"])
    state["documents"][0].warnings.append("SOURCE_CHANGED")
    changed = prepare_extraction(state)
    assert set(changed["active_chunk_keys"]).isdisjoint(before["active_chunk_keys"])
    assert all(
        changed["chunk_progress"][key].status == "pending" for key in changed["active_chunk_keys"]
    )


def test_save_receipt_checkpoint_resumes_cleanup_without_duplicate_save(tmp_path):
    saver, client = Saver(), Embedder()
    compiled = graph(
        Model(),
        saver=saver,
        client=client,
        checkpointer=InMemorySaver(),
        interrupt_before=["complete_document"],
    )
    config = {"configurable": {"thread_id": "save-before-cleanup"}}
    item = source(tmp_path, 1, "One complete development incident.")

    async def run():
        await compiled.ainvoke(inputs([item]), config)
        before = compiled.get_state(config)
        assert before.next == ("complete_document",)
        assert before.values["document_save_receipt"] is not None
        assert before.values["chunk_progress"]
        return await compiled.ainvoke(None, config)

    state = asyncio.run(run())
    assert len(saver.requests) == len(client.calls) == 1
    assert state["document_progress"][str(UUID(int=1))].status == "completed"
    assert not state["chunk_progress"]


def test_draft_failure_preserves_other_experiences_and_blocks_only_its_document(tmp_path):
    class BrokenWriter(Model):
        async def generate(self, **kwargs):
            payload = kwargs["payload"]
            if "extraction" in payload and "BROKEN" in payload["extraction"]["context"]["value"]:
                raise GenerationError("LLM_INCOMPLETE", "Synthetic drafting failure")
            return await super().generate(**kwargs)

    items = [
        source(tmp_path, 1, "BROKEN development incident."),
        source(tmp_path, 2, "Independent development incident."),
    ]
    state = asyncio.run(graph(BrokenWriter()).ainvoke(inputs(items)))
    assert len(state["drafts"]) == len(state["embeddings"]) == 1
    assert state["processing_failures"][0].stage == "draft_card"
    assert state["processing_failures"][0].experience_id
    assert state["document_progress"][str(UUID(int=1))].status == "incomplete"
    assert state["document_progress"][str(UUID(int=2))].status == "ready_to_save"


def test_unexpected_programming_error_is_not_hidden_as_a_document_failure(tmp_path):
    class Bug(Model):
        async def generate(self, **kwargs):
            raise TypeError("Synthetic programming error")

    item = source(tmp_path, 1, "One complete development incident.")
    with pytest.raises(TypeError, match="programming error"):
        asyncio.run(graph(Bug()).ainvoke(inputs([item])))


def test_gate_extract_repair_keeps_other_chunks_without_recalling_them(tmp_path):
    class Repair(Model):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            if "claims" in kwargs["payload"]:
                for item in result.assessments:
                    claim = next(
                        row for row in kwargs["payload"]["claims"] if row["key"] == item.key
                    )
                    if item.key == "extract.problem" and "REPAIR" in claim["value"]:
                        item.verdict, item.failure_kind = "unsupported", "claim"
            return result

    model = Repair()
    item = source(tmp_path, 1, "A supported independent incident.\n\nREPAIR another incident.")
    state = asyncio.run(graph(model).ainvoke(inputs([item])))
    assert sorted(model.calls.values()) == [1, 2]
    assert len(state["chunks"]) == len(state["all_chunks"]) == 2
    assert len(state["verified_drafts"]) == len(state["review_drafts"]) == 1
    assert state["document_progress"][str(UUID(int=1))].status == "needs_review"


def test_partial_completion_cannot_delete_other_documents_checkpoints(tmp_path):
    checkpointer = InMemorySaver()
    compiled = graph(Model(), saver=Saver(fail=UUID(int=2)), checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "keep-incomplete"}}
    items = [source(tmp_path, i, f"Complete development incident {i}.") for i in (1, 2)]

    async def run():
        state = await compiled.ainvoke(inputs(items), config)
        with pytest.raises(ValueError):
            await release_completed_checkpoints(
                state,
                checkpointer,
                "keep-incomplete",
                job_result_recorded=True,
            )
        assert list(checkpointer.list(config))

    asyncio.run(run())


def test_fake_or_partial_save_response_cannot_trigger_cleanup(tmp_path):
    async def uncommitted(request):
        return {
            "document_id": request.document.document_id,
            "payload_hash": request.payload_hash,
            "committed": False,
            "result_recorded": True,
            "card_ids": [],
        }

    item = source(tmp_path, 1, "Complete development incident.")
    state = asyncio.run(graph(Model(), saver=uncommitted).ainvoke(inputs([item])))
    assert state["document_progress"][str(UUID(int=1))].status == "incomplete"
    assert state["chunk_progress"] and state["drafts"]
