import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.domains.documents.pipeline.devtools.demo import DemoGenerator, get_demo_graph
from app.domains.documents.pipeline.embedding_texts import (
    CardEmbeddingTexts,
    EmbeddingTextOptions,
    build_embedding_texts,
)
from app.domains.documents.pipeline.execution import initialize
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.models import (
    FACT_FIELDS,
    ExtractedElements,
    KnowledgeCardDraft,
)
from app.domains.documents.pipeline.nodes.build_embedding_texts import (
    build_embedding_texts as build_texts_node,
)
from app.domains.documents.pipeline.nodes.chunk import chunk
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.verification.models import (
    VerificationIssue,
    VerificationOptions,
    VerificationResult,
)


def make_draft(**changes):
    values = {
        "context": "FastAPI service",
        "problem": "Requests stalled",
        "cause": "Pool exhausted",
        "approach": "Bound concurrency",
        "rationale": "Match pool capacity",
        "outcome": "Requests recovered",
        "lesson": "Limit concurrent work",
        **changes,
    }
    source = ExtractedElements(
        **{
            name: {"value": value, "status": "confirmed", "evidence_refs": ["e1"]}
            if value is not None
            else {}
            for name, value in values.items()
        },
        evidence_index={
            "e1": {
                "document_id": "4c10a229-9b3a-40bc-8611-e2f787540f03",
                "block_id": "block-1",
                "location": "original.md:1",
                "excerpt": "EXCLUDED_SOURCE_TEXT",
            }
        },
        technologies=["EXCLUDED_TECH_TAG"],
    )
    return KnowledgeCardDraft(
        experience_id="experience-1",
        source_extraction=source,
        field_evidence_refs={
            "title": ["e1"],
            "summary": ["e1"],
            **{name: ["e1"] if values[name] is not None else [] for name in FACT_FIELDS},
        },
        lesson_status="confirmed",
        title="Pool recovery",
        summary="Restore request handling",
        technologies=list(source.technologies),
        keywords=["EXCLUDED_KEYWORD"],
        **values,
    )


def decision(draft, value="pass"):
    issues = (
        []
        if value == "pass"
        else [
            VerificationIssue(
                check="consistency",
                code="TEST_NOT_PASSED",
                field="title",
                reason="Test refusal",
                action="Review",
                disposition=value,
                retry_node="draft_card" if value == "retry" else None,
            )
        ]
    )
    return VerificationResult(
        experience_id=draft.experience_id,
        decision=value,
        issues=issues,
        retry_node="draft_card" if value == "retry" else None,
    )


def test_exact_four_combinations_and_field_order_without_metadata():
    draft = make_draft()
    plan = build_embedding_texts(draft)
    assert list(plan.texts) == ["content", "situation", "solution", "lesson"]
    assert not plan.skipped
    assert plan.template_version == "card-views-v1"
    assert plan.texts["content"].text == (
        "[title]\nPool recovery\n\n[summary]\nRestore request handling\n\n"
        "[context]\nFastAPI service\n\n[problem]\nRequests stalled\n\n"
        "[cause]\nPool exhausted\n\n[approach]\nBound concurrency\n\n"
        "[rationale]\nMatch pool capacity\n\n[outcome]\nRequests recovered\n\n"
        "[lesson]\nLimit concurrent work"
    )
    assert plan.texts["situation"].text == (
        "[context]\nFastAPI service\n\n[problem]\nRequests stalled"
    )
    assert plan.texts["solution"].text == (
        "[problem]\nRequests stalled\n\n[cause]\nPool exhausted\n\n"
        "[approach]\nBound concurrency\n\n[rationale]\nMatch pool capacity\n\n"
        "[outcome]\nRequests recovered"
    )
    assert plan.texts["lesson"].text == (
        "[context]\nFastAPI service\n\n[problem]\nRequests stalled\n\n"
        "[lesson]\nLimit concurrent work"
    )
    assert "cause" not in plan.texts["situation"].included_fields
    assert "cause" not in plan.texts["lesson"].included_fields
    assert "context" not in plan.texts["solution"].included_fields
    assert "EXCLUDED" not in "\n".join(item.text for item in plan.texts.values())


@pytest.mark.parametrize(
    "changes,ready,missing",
    [
        (
            {"problem": None},
            {"content"},
            {"situation": ("problem",), "solution": ("problem",), "lesson": ("problem",)},
        ),
        ({"approach": None}, {"content", "situation", "lesson"}, {"solution": ("approach",)}),
        (
            {"cause": None, "rationale": None, "outcome": None},
            {"content", "situation", "solution", "lesson"},
            {},
        ),
    ],
)
def test_view_minimums_skip_only_unavailable_views(changes, ready, missing):
    plan = build_embedding_texts(make_draft(**changes))
    assert set(plan.texts) == ready
    assert set(plan.skipped) == set(missing)
    for view, fields in missing.items():
        assert plan.skipped[view].code == "INSUFFICIENT_FIELDS"
        assert plan.skipped[view].missing_fields == fields
    for text in plan.texts.values():
        assert "None" not in text.text and "unknown" not in text.text
        for name in changes:
            assert name not in text.included_fields


def test_no_experience_facts_cannot_generate_content_from_generic_title_and_lesson():
    draft = make_draft(problem=None, cause=None, approach=None, rationale=None, outcome=None)
    plan = build_embedding_texts(draft)
    assert not plan.texts and len(plan.skipped) == 4
    assert plan.skipped["content"].required_any_of == (
        "problem",
        "cause",
        "approach",
        "rationale",
        "outcome",
    )


def test_preserves_korean_code_logs_whitespace_and_technical_context():
    value = "  한글 로그 / FastAPI\n```python\n    await run()\n```\n  "
    draft = make_draft(approach=value)
    before = draft.model_dump()
    first = build_embedding_texts(draft)
    second = build_embedding_texts(draft)
    assert first == second and draft.model_dump() == before
    assert f"[approach]\n{value}" in first.texts["solution"].text
    assert CardEmbeddingTexts.model_validate_json(first.model_dump_json()) == first


def test_hypothesized_cause_is_not_reintroduced_from_extraction_snapshot():
    draft = make_draft(cause=None)
    draft.source_extraction.cause = draft.source_extraction.problem.model_copy(
        update={"status": "hypothesis", "value": "UNCONFIRMED_CAUSE"}
    )
    plan = build_embedding_texts(draft)
    assert "cause" not in plan.texts["solution"].included_fields
    assert "UNCONFIRMED_CAUSE" not in plan.model_dump_json()
    assert draft.source_extraction.cause.status == "hypothesis"


def test_generalized_lesson_keeps_its_inferred_status_and_original_evidence():
    draft = make_draft()
    draft.lesson = "Generalize concurrency limits to match downstream capacity"
    draft.lesson_status = "inferred"
    before = draft.model_dump()
    result = build_embedding_texts(draft)
    assert draft.model_dump() == before
    assert draft.lesson in result.texts["lesson"].text
    assert draft.lesson_status == "inferred"


def test_character_limit_skips_without_truncation_and_allows_exact_boundary():
    draft = make_draft()
    original = build_embedding_texts(draft)
    size = len(original.texts["situation"].text)
    plan = build_embedding_texts(draft, options=EmbeddingTextOptions(max_chars=size))
    assert plan.texts["situation"] == original.texts["situation"]
    assert plan.skipped["content"].code == "INPUT_TOO_LARGE"
    assert plan.skipped["content"].text_chars == len(original.texts["content"].text)
    assert len(build_embedding_texts(draft, options=EmbeddingTextOptions(max_chars=1)).skipped) == 4


@pytest.mark.parametrize("value", [0, -1, True, "100"])
def test_invalid_character_limit_is_rejected(value):
    with pytest.raises(ValidationError):
        EmbeddingTextOptions(max_chars=value)


def test_revalidates_mutated_draft_instead_of_embedding_invalid_or_unconfirmed_values():
    draft = make_draft()
    draft.context = "   "
    with pytest.raises(ValidationError):
        build_embedding_texts(draft)
    draft = make_draft(cause=None)
    draft.cause = "Invented cause"
    with pytest.raises(ValidationError):
        build_embedding_texts(draft)


def test_node_uses_only_passed_drafts_and_replaces_old_results():
    draft, review = make_draft(), make_draft()
    review.experience_id = "review-2"
    state = {
        "drafts": [draft, review],
        "verified_drafts": [draft],
        "review_drafts": [review],
        "verification_results": [decision(draft), decision(review, "needs_review")],
    }
    first = build_texts_node(state)
    assert len(first["embedding_texts"]) == 1
    assert first["embedding_texts"][0].experience_id == draft.experience_id
    assert build_texts_node({**state, **first}) == first
    assert build_texts_node({"embedding_texts": first["embedding_texts"]}) == {
        "embedding_texts": []
    }


@pytest.mark.parametrize("value", ["retry", "reject", "needs_review", None])
def test_node_rejects_admission_without_matching_final_pass(value):
    draft = make_draft()
    state = {
        "verified_drafts": [draft],
        "verification_results": [] if value is None else [decision(draft, value)],
    }
    with pytest.raises(ValueError, match="matching final Gate pass"):
        build_texts_node(state)


def test_duplicate_experiences_or_conflicting_decisions_fail_closed():
    draft = make_draft()
    with pytest.raises(ValueError, match="unique admitted"):
        build_texts_node(
            {"verified_drafts": [draft, draft], "verification_results": [decision(draft)]}
        )
    with pytest.raises(ValueError, match="unique final"):
        build_texts_node(
            {
                "verified_drafts": [draft],
                "verification_results": [decision(draft), decision(draft, "needs_review")],
            }
        )


def test_upstream_and_new_job_clear_stale_texts():
    stale = [build_embedding_texts(make_draft())]
    assert initialize({"sources": [], "embedding_texts": stale})["embedding_texts"] == []
    assert chunk({"documents": [], "embedding_texts": stale})["embedding_texts"] == []


def test_actual_demo_graph_builds_only_final_repaired_card_views_without_api_calls():
    result = asyncio.run(get_demo_graph().ainvoke({}))
    assert result["verification_results"][0].decision == "pass"
    assert len(result["embedding_texts"]) == len(result["verified_drafts"]) == 1
    plan = result["embedding_texts"][0]
    assert len(plan.texts) == 4
    assert "Redis" not in plan.model_dump_json()
    assert plan.experience_id == result["verified_drafts"][0].experience_id


@pytest.mark.parametrize("llm_verification,expected", [(True, 2), (False, 0)])
def test_actual_graph_keeps_independent_experiences_and_excludes_rule_only_reviews(
    llm_verification, expected
):
    generator = DemoGenerator()
    graph = build_graph(
        extract_node=make_extract_node(generator),
        draft_node=make_draft_node(generator),
        verify_node=make_verify_node(
            options=VerificationOptions(llm_enabled=llm_verification),
            generator=generator if llm_verification else None,
        ),
    )
    path = Path("app/domains/documents/pipeline/devtools/demo.md").resolve()
    result = asyncio.run(
        graph.ainvoke(
            {
                "sources": [
                    {
                        "document_id": str(UUID(int=index)),
                        "original_filename": path.name,
                        "path": str(path),
                    }
                    for index in (1, 2)
                ]
            }
        )
    )
    assert len(result["embedding_texts"]) == expected
    assert {plan.experience_id for plan in result["embedding_texts"]} == {
        draft.experience_id for draft in result["verified_drafts"]
    }
    if not llm_verification:
        assert len(result["review_drafts"]) == 2


def test_empty_graph_and_checkpoint_new_input_do_not_reuse_old_texts():
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "embedding-new-job"}}

    async def run():
        await graph.aupdate_state(
            config, {"embedding_texts": [build_embedding_texts(make_draft())]}
        )
        result = await graph.ainvoke(
            {"sources": [], "embedding_text_options": {"max_chars": 20}}, config
        )
        assert result["embedding_texts"] == []
        assert result["embedding_text_options"].max_chars == 20
        result = await graph.ainvoke(None, config)
        assert result["embedding_texts"] == []

    asyncio.run(run())
