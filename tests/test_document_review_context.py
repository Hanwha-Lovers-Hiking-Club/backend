"""추가 문맥의 범위·예산·근거 분리와 의미 검증 진단 기록을 검사한다."""

import asyncio
from copy import deepcopy

import pytest

from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.verification.models import VerificationOptions
from tests.test_document_verification import Verifier, redraft
from tests.test_document_verification import state as state


def add_context(state, *, text="The prototype was tested and the rendering was verified."):
    doc = state["documents"][0]
    block = doc.blocks[0].model_copy(
        update={
            "order": len(doc.blocks),
            "block_id": "later-result-block",
            "evidence_id": "later-result-evidence",
            "location": "later section",
            "text": text,
        }
    )
    ref = doc.evidence_index[doc.blocks[0].evidence_id].model_copy(
        update={
            "block_id": block.block_id,
            "location": block.location,
            "excerpt": text,
            "start_char": None,
            "end_char": None,
        }
    )
    doc.blocks.append(block)
    doc.evidence_index[block.evidence_id] = ref
    return block


def verify(state, model, *, context_max_chars=12000, max_input_chars=48000):
    return asyncio.run(
        make_verify_node(
            options=VerificationOptions(
                llm_enabled=True,
                context_max_chars=context_max_chars,
                max_input_chars=max_input_chars,
            ),
            generator=model,
        )(state)
    )


def test_later_section_is_review_context_without_becoming_assigned_evidence(state):
    block = add_context(state)
    before = deepcopy(state["drafts"][0].field_evidence_refs)
    model = Verifier()
    result = verify(state, model)
    payload = model.calls[0]
    assert block.evidence_id not in payload["sources"]
    assert payload["review_context"]["blocks"][block.evidence_id]["text"] == block.text
    assert all(x["coverage"] == "complete" for x in payload["review_context"]["documents"].values())
    assert state["drafts"][0].field_evidence_refs == before
    trace = result["verification_traces"][0]
    assert trace.context_block_ids == [block.block_id]
    assert trace.report_valid and trace.error_code is None
    assert trace.assessments and all(x.verdict == "supported" for x in trace.assessments)
    assert all(x.reason for x in trace.assessments)


class ContextVerifier(Verifier):
    def __init__(self, *, scope=False, counter=None, promote=False, **kwargs):
        super().__init__(**kwargs)
        self.scope, self.counter, self.promote = scope, counter, promote

    async def generate(self, **kwargs):
        report = await super().generate(**kwargs)
        item = next(x for x in report.assessments if x.key == self.key)
        item.requires_document_scope = self.scope
        if self.counter:
            item.counter_evidence_refs = [self.counter]
        if self.promote:
            item.evidence_refs = ["later-result-evidence"]
        return report


@pytest.mark.parametrize("size", [0, 300])
def test_document_scope_claim_cannot_pass_when_context_is_partial(state, size):
    add_context(state, text="Additional source information. " * 100)
    result = verify(state, ContextVerifier(scope=True), context_max_chars=size)
    assert result["verification_results"][0].decision == "needs_review"
    assert result["verification_results"][0].issues[0].code == "DOCUMENT_SCOPE_NOT_VERIFIED"
    trace = result["verification_traces"][0]
    assert set(trace.document_coverage.values()) == {"partial"}
    assert trace.input_chars <= 48000
    assert trace.report_valid


def test_context_fits_both_its_budget_and_total_request_budget(state):
    add_context(state, text="An oversized supplementary block. " * 1000)
    model = Verifier()
    result = verify(state, model, context_max_chars=1000, max_input_chars=15000)
    trace = result["verification_traces"][0]
    assert trace.context_block_ids == []
    assert trace.input_chars <= 15000
    assert set(trace.document_coverage.values()) == {"partial"}
    assert result["verification_results"][0].decision == "pass"


def test_whole_document_claim_with_complete_coverage_can_be_supported(state):
    add_context(state)
    result = verify(state, ContextVerifier(scope=True))
    assert result["verification_results"][0].decision == "pass"
    assert set(result["verification_traces"][0].document_coverage.values()) == {"complete"}


def test_counter_evidence_is_recorded_separately_from_supporting_evidence(state):
    block = add_context(state)
    model = ContextVerifier(verdict="unsupported", key="extract.outcome", counter=block.evidence_id)
    result = verify(state, model)
    assert result["verification_results"][0].retry_node == "extract"
    assessment = next(
        x for x in result["verification_traces"][0].assessments if x.key == "extract.outcome"
    )
    assert assessment.counter_evidence_refs == [block.evidence_id]
    assert block.evidence_id not in assessment.evidence_refs


@pytest.mark.parametrize("case", ["unknown_counter", "context_as_support"])
def test_unassigned_or_invented_ids_cannot_be_used_to_approve_a_card(state, case):
    add_context(state)
    model = ContextVerifier(
        verdict="unsupported" if case == "unknown_counter" else "supported",
        counter="fabricated-evidence" if case == "unknown_counter" else None,
        promote=case == "context_as_support",
    )
    result = verify(state, model)
    trace = result["verification_traces"][0]
    assert not trace.report_valid and trace.error_code == "INVALID_VERIFIER_REPORT"
    assert result["verification_results"][0].decision == "needs_review"


def test_existing_and_new_pass_reasons_survive_redrafting(state):
    first = verify(state, Verifier())
    state.update(first)
    redraft(state)
    second = verify(state, Verifier())
    assert len(first["verification_traces"]) == 1
    assert len(second["verification_traces"]) == 2
    assert second["verification_history"] == []


@pytest.mark.parametrize(
    "outcome",
    ["The design was Accepted.", "No validation results are provided in the document."],
)
def test_semantic_outcome_failure_reprocesses_extract_not_only_the_title(state, outcome):
    source = state["extraction_result"].experiences[0]
    redraft(
        state,
        outcome={
            "value": outcome,
            "status": "confirmed",
            "evidence_refs": source.outcome.evidence_refs,
        },
    )
    model = Verifier("unsupported", key="extract.outcome")
    result = verify(state, model)
    assert result["verification_results"][0].retry_node == "extract"
    assert model.calls[0]["source_extraction"]["outcome"]["value"] == outcome


def test_explicit_negative_source_is_not_blocked_by_a_keyword_rule(state):
    source = state["extraction_result"].experiences[0]
    block = state["documents"][0].blocks[5]
    block.text = "No production test was performed."
    ref = state["documents"][0].evidence_index[block.evidence_id]
    ref.excerpt, ref.end_char = block.text, len(block.text)
    source.evidence_index[block.evidence_id] = ref.model_copy(deep=True)
    redraft(
        state,
        outcome={"value": block.text, "status": "confirmed", "evidence_refs": [block.evidence_id]},
    )
    result = verify(state, Verifier())
    assert result["verification_results"][0].decision == "pass"
