import asyncio
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.domains.documents.pipeline.devtools.demo import (
    DemoGenerator,
    get_demo_graph,
    skip_demo_embedding,
)
from app.domains.documents.pipeline.devtools.web import app as viewer_app
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.verification.models import VerificationOptions


class Model(DemoGenerator):
    def __init__(
        self, *, fail_kind=None, fail_key="draft.title", second_decision="retry", empty_retry=False
    ):
        self.calls = []
        self.fail_kind, self.fail_key = fail_kind, fail_key
        self.second_decision, self.empty_retry = second_decision, empty_retry

    async def generate(self, *, instructions, payload, output_type):
        self.calls.append(deepcopy(payload))
        if "blocks" in payload and self.empty_retry and payload.get("retry_feedback"):
            return output_type.model_validate({"experiences": []})
        if "extraction" in payload and self.second_decision == "pass":
            document_id = next(iter(payload["extraction"]["evidence_index"].values()))[
                "document_id"
            ]
            if document_id.endswith("000002"):
                payload = {**payload, "retry_feedback": [{"demo": True}]}
        result = await super().generate(
            instructions=instructions, payload=payload, output_type=output_type
        )
        if "claims" in payload:
            for row in result.assessments:
                if self.second_decision == "review":
                    document_id = next(iter(payload["sources"].values()))["evidence"]["document_id"]
                    if document_id.endswith("000002") and row.key == "draft.title":
                        row.verdict = "uncertain"
                        continue
                if row.key == self.fail_key and any(
                    "Redis" in claim["value"] for claim in payload["claims"]
                ):
                    row.verdict = "unsupported"
                    row.failure_kind = self.fail_kind
        return result


@pytest.fixture
def sources(tmp_path):
    template = Path("app/domains/documents/pipeline/devtools/demo.md").read_bytes()
    result = []
    for index in (1, 2):
        path = tmp_path / f"source-{index}.md"
        path.write_bytes(template)
        result.append(
            {"document_id": str(UUID(int=index)), "original_filename": path.name, "path": str(path)}
        )
    return result


def graph_for(model, **kwargs):
    return build_graph(
        embed_node=skip_demo_embedding,
        extract_node=make_extract_node(model),
        draft_node=make_draft_node(model),
        verify_node=make_verify_node(
            options=VerificationOptions(llm_enabled=True), generator=model
        ),
        **kwargs,
    )


def invoke(graph, sources):
    async def run():
        visited, result = [], None
        async for update in graph.astream({"sources": sources}, stream_mode="updates"):
            visited.extend(update)
            if "finish" in update:
                result = update["finish"]
        return visited, result

    return asyncio.run(run())


def test_real_langgraph_executes_draft_repair_and_finishes(sources):
    visited, result = invoke(graph_for(Model()), sources[:1])
    assert visited.count("draft_card") == 2 and visited.count("verify") == 2
    assert visited.count("extract") == 1 and visited.count("parse") == 1
    assert result["verification_results"][0].decision == "pass"
    assert len(result["verification_history"]) == 1 and len(result["verified_drafts"]) == 1
    assert not result["retry_requests"]


def test_two_queued_independent_repairs_keep_both_final_cards(sources):
    visited, result = invoke(graph_for(Model()), sources)
    assert visited.count("prepare_retry") == 2
    assert len(result["verified_drafts"]) == 2 and len(result["verification_history"]) == 2
    assert len({draft.experience_id for draft in result["verified_drafts"]}) == 2
    assert len(result["documents"]) == 2 and len(result["chunks"]) == 2


@pytest.mark.parametrize("second_decision,expected", [("pass", 2), ("review", 1)])
def test_other_candidate_decisions_survive_scoped_repair(sources, second_decision, expected):
    model = Model(second_decision=second_decision)
    _, result = invoke(graph_for(model), sources)
    assert len(result["verified_drafts"]) == expected
    assert len(result["review_drafts"]) == (second_decision == "review")
    assert len([call for call in model.calls if "extraction" in call]) == 3


@pytest.mark.parametrize(
    "kind,target", [("context_missing", "chunk"), ("source_unreadable", "parse")]
)
def test_conditionally_requested_upstream_node_actually_executes(sources, kind, target):
    visited, result = invoke(graph_for(Model(fail_kind=kind)), sources[:1])
    assert visited.count(target) == 2
    assert len(result["verification_history"]) == 1
    assert result["verification_results"][0].decision == "needs_review"
    assert not result["verified_drafts"]


def test_extract_repair_can_produce_zero_candidates_without_fake_success(sources):
    visited, result = invoke(
        graph_for(Model(fail_key="extract.cause", empty_retry=True)), sources[:1]
    )
    assert visited.count("extract") == 2 and visited.count("parse") == 1
    assert result["verification_results"][0].decision == "reject"
    assert result["verification_results"][0].issues[0].code == "RETRY_NO_DRAFT"
    assert not result["verified_drafts"] and result["extraction_result"].experiences


def test_unchanged_draft_stops_without_paying_for_another_verification(sources):
    model = Model()

    async def unchanged_writer(state):
        return await make_draft_node(model)({**state, "retry_feedback": []})

    graph = build_graph(
        embed_node=skip_demo_embedding,
        extract_node=make_extract_node(model),
        draft_node=unchanged_writer,
        verify_node=make_verify_node(
            options=VerificationOptions(llm_enabled=True), generator=model
        ),
    )
    visited, result = invoke(graph, sources[:1])
    assert visited.count("prepare_retry") == 1
    assert len([call for call in model.calls if "claims" in call]) == 1
    assert result["verification_results"][0].decision == "needs_review"


def test_normalization_error_routes_to_normalize_without_reparsing(sources):
    from app.domains.documents.pipeline.verification.gate import verify_state

    model, calls = Model(), []

    async def verifier(state):
        if not calls:
            ref = next(iter(state["documents"][0].evidence_index.values()))
            ref.location = "invalid source position"
        calls.append(True)
        return await verify_state(
            state, options=VerificationOptions(llm_enabled=True), generator=model
        )

    graph = build_graph(
        embed_node=skip_demo_embedding,
        extract_node=make_extract_node(model),
        draft_node=make_draft_node(model),
        verify_node=verifier,
    )
    visited, result = invoke(graph, sources[:1])
    assert visited.count("normalize") == 2 and visited.count("parse") == 1
    assert result["verification_results"][0].decision == "pass"
    assert len(result["verification_history"]) == 2


def test_checkpoint_resume_does_not_restart_parse_or_extract(sources):
    model = Model()
    graph = graph_for(model, checkpointer=InMemorySaver(), interrupt_before=["verify"])
    config = {"configurable": {"thread_id": "resume-test"}}

    async def run():
        await graph.ainvoke({"sources": sources[:1]}, config)
        assert graph.get_state(config).next == ("verify",)
        await graph.ainvoke(None, config)
        assert graph.get_state(config).next == ("verify",)
        result = await graph.ainvoke(None, config)
        assert not graph.get_state(config).next
        return result

    result = asyncio.run(run())
    assert len(result["verified_drafts"]) == 1
    assert len([call for call in model.calls if "blocks" in call]) == 1
    assert len([call for call in model.calls if "extraction" in call]) == 2


def test_node_error_propagates_instead_of_reaching_finish(sources):
    class Broken:
        async def generate(self, **kwargs):
            raise GenerationError("LLM_API_ERROR", "failed")

    with pytest.raises(GenerationError, match="failed"):
        invoke(graph_for(Broken()), sources[:1])


def test_no_sources_finishes_without_model_calls():
    class Forbidden:
        async def generate(self, **kwargs):
            pytest.fail("No model calls for an empty source list")

    _, result = invoke(graph_for(Forbidden()), [])
    assert not result["verified_drafts"] and not result["verification_results"]


def test_local_viewer_runs_actual_demo_graph_without_api_keys():
    client = TestClient(viewer_app)
    assert client.get("/dev").status_code == 200
    topology = client.get("/dev/graph").json()
    assert "prepare_retry" in topology["nodes"]
    assert any(
        edge["source"] == "prepare_retry" and edge["target"] == "draft_card"
        for edge in topology["edges"]
    )
    result = client.post("/dev/demo").json()
    assert result["is_demo"] is True and result["saved_count"] == 0
    assert result["retry_count"] == 1 and result["admitted_count"] == 1
    assert result["visited_nodes"].count("draft_card") == 2


def test_reextracting_shared_document_keeps_settled_experience_without_duplicates(sources):
    class SharedDocumentModel(Model):
        async def generate(self, *, instructions, payload, output_type):
            if (
                "extraction" in payload
                and payload["extraction"]["problem"]["value"] != "Requests stalled."
            ):
                payload = {**payload, "retry_feedback": [{"demo": True}]}
            result = await super().generate(
                instructions=instructions, payload=payload, output_type=output_type
            )
            if "blocks" in payload:
                second = result.experiences[0].model_copy(deep=True)
                second.problem = second.cause.model_copy(deep=True)
                result.experiences.append(second)
            return result

    visited, result = invoke(graph_for(SharedDocumentModel(fail_key="extract.cause")), sources[:1])
    assert visited.count("extract") == 2
    assert len(result["verified_drafts"]) == 1 and len(result["review_drafts"]) == 1
    assert len({item.experience_id for item in result["drafts"]}) == 2


def test_duplicate_source_identity_is_rejected_before_execution(sources):
    with pytest.raises(ValueError, match="unique document IDs"):
        invoke(graph_for(Model()), [sources[0], sources[0]])


def test_visualized_edges_are_compiled_from_the_same_graph():
    graph = get_demo_graph().get_graph()
    assert any(edge.source == "settle" and edge.target == "prepare_retry" for edge in graph.edges)
