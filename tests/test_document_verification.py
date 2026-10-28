import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domains.documents.pipeline.document import ParsedBlock, ParsedDocument
from app.domains.documents.pipeline.drafting import draft_state
from app.domains.documents.pipeline.extraction import experience_id
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.models import FACT_FIELDS, ExtractedElements, ExtractionResult
from app.domains.documents.pipeline.nodes.verify import make_verify_node, verify
from app.domains.documents.pipeline.normalization import normalize_document
from app.domains.documents.pipeline.verification.models import (
    VerificationOptions,
    VerificationResult,
)

DOC_ID = "4c10a229-9b3a-40bc-8611-e2f787540f03"


class Writer:
    def __init__(self, title=None):
        self.title = title
        self.calls = []

    async def generate(self, *, instructions, payload, output_type):
        self.calls.append(deepcopy(payload))
        source = payload["extraction"]
        return output_type.model_validate(
            {
                "draft": {
                    "title": self.title or source["problem"]["value"],
                    "summary": source["outcome"]["value"],
                    "lesson": source["lesson"]["value"],
                    "keywords": ["health"],
                },
                "skip_reason": None,
            }
        )


class Verifier:
    def __init__(self, verdict="supported", key="draft.title", invalid=None, failure_kind=None):
        self.verdict, self.key, self.invalid = verdict, key, invalid
        self.failure_kind = failure_kind
        self.calls = []

    async def generate(self, *, instructions, payload, output_type):
        self.calls.append(deepcopy(payload))
        rows = [
            {
                "key": claim["key"],
                "verdict": self.verdict if claim["key"] == self.key else "supported",
                "evidence_refs": claim["evidence_refs"],
                "reason": "Compared with the original block",
                "failure_kind": self.failure_kind if claim["key"] == self.key else None,
            }
            for claim in payload["claims"]
        ]
        if self.invalid == "missing":
            rows.pop()
        elif self.invalid == "duplicate":
            rows.append(rows[0])
        elif self.invalid == "foreign":
            rows[0]["evidence_refs"] = ["unknown-id"]
        return output_type.model_validate({"assessments": rows})


def forbidden_generator():
    class Forbidden:
        async def generate(self, **kwargs):
            pytest.fail("This case must not invoke a model")

    return Forbidden()


@pytest.fixture
def state():
    values = [
        "FastAPI service.",
        "Requests stalled.",
        "Database was unavailable.",
        "Added a health check.",
        "Detect dependency failures.",
        "Requests recovered.",
        "Check dependency health.",
    ]
    document = normalize_document(
        ParsedDocument(
            document_id=DOC_ID,
            original_filename="original.md",
            blocks=[
                ParsedBlock(block_type="paragraph", text=text, location=f"line {index + 1}")
                for index, text in enumerate(values)
            ],
        )
    )
    source = ExtractedElements(
        **{
            name: {"value": text, "status": "confirmed", "evidence_refs": [block.evidence_id]}
            for name, text, block in zip(FACT_FIELDS, values, document.blocks, strict=True)
        },
        evidence_index={
            key: ref.model_copy(deep=True) for key, ref in document.evidence_index.items()
        },
        technologies=["FastAPI"],
        keywords=["dependency"],
    )
    result = {"documents": [document], "extraction_result": ExtractionResult(experiences=[source])}
    result.update(asyncio.run(draft_state(result, Writer())))
    return result


def run(state, *, llm=False, model=None, max_retries=2):
    return asyncio.run(
        make_verify_node(
            options=VerificationOptions(llm_enabled=llm, max_retries=max_retries),
            generator=model,
        )(state)
    )


def codes(result):
    return {item.code for item in result["verification_results"][0].issues}


def redraft(state, **source_changes):
    data = state["extraction_result"].experiences[0].model_dump()
    data.update(source_changes)
    source = ExtractedElements.model_validate(data)
    state["extraction_result"] = ExtractionResult(experiences=[source])
    state.update(asyncio.run(draft_state(state, Writer())))
    return state


def test_rules_admit_literal_grounded_draft_without_mutating_state_or_scoring(state):
    before = deepcopy(state)
    result = run(state, model=forbidden_generator())
    assert result["verification_results"][0].decision == "pass"
    assert result["verified_drafts"] == state["drafts"]
    assert not result["review_drafts"] and not result["retry_requests"]
    assert state == before
    assert result["verified_drafts"][0] is not state["drafts"][0]
    fields = result["verified_drafts"][0].to_card_fields()
    assert fields["status"] == "draft"
    assert "quality_score" not in fields and "last_verified_at" not in fields


def test_no_candidates_does_not_call_verifier():
    assert run({"drafts": []}, llm=True, model=forbidden_generator())["verified_drafts"] == []


@pytest.mark.parametrize("change", [{"title": 123}, {"title": " "}, {"unexpected": True}])
def test_invalid_draft_types_or_fields_request_rewrite(state, change):
    state["drafts"] = [{**state["drafts"][0].model_dump(), **change}]
    result = run(state, llm=True, model=forbidden_generator())
    assert result["verification_results"][0].retry_node == "draft_card"
    assert "INVALID_DRAFT" in codes(result)
    assert not result["verified_drafts"]


def test_wrong_but_existing_field_evidence_is_rejected(state):
    draft = state["drafts"][0].model_dump()
    draft["field_evidence_refs"]["problem"] = draft["field_evidence_refs"]["context"]
    with pytest.raises(ValidationError, match="that field's source evidence"):
        type(state["drafts"][0]).model_validate(draft)
    state["drafts"] = [draft]
    assert "INVALID_DRAFT" in codes(run(state))


def test_minimum_problem_solving_information_is_required(state):
    draft = state["drafts"][0].model_dump()
    for name in FACT_FIELDS[1:-1]:
        draft[name] = None
        draft["source_extraction"][name] = {"value": None, "status": "unknown", "evidence_refs": []}
        draft["field_evidence_refs"][name] = []
    state["drafts"] = [draft]
    result = run(state)
    assert result["verification_results"][0].decision == "reject"
    assert not result["retry_requests"]


def test_optional_quote_offsets_can_be_omitted(state):
    source = state["extraction_result"].experiences[0]
    for ref in source.evidence_index.values():
        ref.start_char = ref.end_char = None
    redraft(state)
    assert run(state)["verification_results"][0].decision == "pass"


def test_missing_original_document_requests_parse(state):
    state["documents"] = []
    result = run(state, llm=True, model=forbidden_generator())
    assert result["verification_results"][0].retry_node == "parse"
    assert "DOCUMENT_NOT_FOUND" in codes(result)


@pytest.mark.parametrize(
    "attribute,value,code,target",
    [
        ("block_id", "missing-block", "EVIDENCE_NOT_FOUND", "extract"),
        ("location", "fake location", "EVIDENCE_SOURCE_MISMATCH", "extract"),
        ("start_char", 999, "QUOTE_MISMATCH", "extract"),
    ],
)
def test_source_locations_blocks_and_spans_are_checked(state, attribute, value, code, target):
    source = state["extraction_result"].experiences[0]
    ref = next(iter(source.evidence_index.values()))
    setattr(ref, attribute, value)
    if attribute == "start_char":
        ref.end_char = value + len(ref.excerpt)
    redraft(state)
    result = run(state, llm=True, model=forbidden_generator())
    assert code in codes(result)
    assert result["verification_results"][0].retry_node == target


def test_quotes_are_compared_with_actual_block_even_if_registries_agree(state):
    source = state["extraction_result"].experiences[0]
    key = source.context.evidence_refs[0]
    source.evidence_index[key].excerpt = "X" * len(source.evidence_index[key].excerpt)
    state["documents"][0].evidence_index[key].excerpt = source.evidence_index[key].excerpt
    redraft(state)
    assert "QUOTE_MISMATCH" in codes(run(state))


def test_replaced_extraction_snapshot_cannot_be_admitted(state):
    state["extraction_result"] = ExtractionResult()
    result = run(state)
    assert "EXTRACTION_SNAPSHOT_MISMATCH" in codes(result)
    assert result["verification_results"][0].retry_node == "extract"


def test_model_construct_or_mutated_document_is_revalidated(state):
    state["documents"][0].blocks[0].order = 99
    assert "INVALID_SOURCE_STATE" in codes(run(state))


def test_invalid_extraction_requests_extract_instead_of_reparsing(state):
    state["extraction_result"].experiences[0].context.status = "invalid-status"
    result = run(state)
    assert "INVALID_EXTRACTION_STATE" in codes(result)
    assert result["verification_results"][0].retry_node == "extract"


def test_nonliteral_generated_claims_need_review_when_llm_is_disabled(state):
    state["drafts"][0].title = "Health checks improved resilience"
    result = run(state, model=forbidden_generator())
    assert result["verification_results"][0].decision == "needs_review"
    assert "SEMANTIC_CHECK_REQUIRED" in codes(result)
    assert not result["verified_drafts"] and result["review_drafts"]


def test_hypothesis_cannot_be_declared_a_confirmed_cause(state):
    ref = state["extraction_result"].experiences[0].cause.evidence_refs
    redraft(
        state,
        cause={"value": "Might be the database.", "status": "confirmed", "evidence_refs": ref},
    )
    result = run(state)
    assert "HYPOTHESIS_AS_FACT" in codes(result)
    assert result["verification_results"][0].retry_node == "extract"


def test_unresolved_fact_conflict_is_held_for_review(state):
    ref = state["extraction_result"].experiences[0].cause.evidence_refs
    redraft(state, cause={"value": None, "status": "conflicted", "evidence_refs": ref})
    result = run(state, llm=True, model=forbidden_generator())
    assert "CONFLICTED_SOURCE" in codes(result)
    assert result["verification_results"][0].decision == "needs_review"


def test_unread_pdf_content_is_not_silently_ignored(state):
    state["documents"][0].warnings = ["TEXT_UNAVAILABLE:p.2"]
    result = run(state, llm=True, model=forbidden_generator())
    assert "INCOMPLETE_SOURCE" in codes(result)
    assert not result["verified_drafts"]


def test_llm_semantic_check_receives_original_blocks_and_retained_fact_states(state):
    model = Verifier()
    result = run(state, llm=True, model=model)
    assert result["verification_results"][0].decision == "pass"
    payload = model.calls[0]
    assert payload["source_extraction"]["cause"]["status"] == "confirmed"
    assert next(iter(payload["sources"].values()))["block"]["text"] == "FastAPI service."


@pytest.mark.parametrize(
    "key,target", [("draft.title", "draft_card"), ("extract.cause", "extract")]
)
def test_unsupported_claims_request_the_responsible_node_with_feedback(state, key, target):
    result = run(state, llm=True, model=Verifier("unsupported", key))
    assert result["verification_results"][0].decision == "retry"
    request = result["retry_requests"][0]
    assert request.target_node == target and request.issues[0].reason
    assert result["retry_feedback"] == [request]


@pytest.mark.parametrize("verdict", ["uncertain", "conflicted"])
def test_uncertain_or_contradictory_llm_findings_are_not_a_pass(state, verdict):
    result = run(state, llm=True, model=Verifier(verdict))
    assert result["verification_results"][0].decision == "needs_review"
    assert not result["verified_drafts"]


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "foreign"])
def test_incomplete_or_fabricated_semantic_reports_are_held(state, invalid):
    result = run(state, llm=True, model=Verifier(invalid=invalid))
    assert "INVALID_VERIFIER_REPORT" in codes(result)


def test_verifier_failure_is_held_instead_of_treated_as_validation_success(state):
    class Failing:
        async def generate(self, **kwargs):
            raise GenerationError("LLM_API_ERROR", "failed")

    assert "LLM_API_ERROR" in codes(run(state, llm=True, model=Failing()))


def test_default_verify_requires_an_explicit_verifier_model_when_enabled(state, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "VERIFY_LLM_ENABLED", True)
    monkeypatch.setattr(settings, "VERIFY_MODEL", None)
    result = asyncio.run(verify(state))
    assert "LLM_NOT_CONFIGURED" in codes(result)
    assert result["verification_results"][0].decision == "needs_review"


def test_same_input_cannot_repeat_a_retry_or_pay_for_another_semantic_call(state):
    first = run(state, llm=True, model=Verifier("unsupported"))
    state.update(first)
    second = run(state, llm=True, model=forbidden_generator())
    assert "UNCHANGED_RETRY_INPUT" in codes(second)
    assert not second["retry_requests"]
    assert len(second["verification_history"]) == 1


def test_changed_inputs_still_obey_job_retry_budget_and_repaired_input_can_pass(state):
    for index in range(3):
        state["drafts"][0].title = f"Unsupported assertion {index}"
        result = run(state, llm=True, model=Verifier("unsupported"), max_retries=2)
        state.update(result)
    assert "RETRY_LIMIT_REACHED" in codes(result)
    assert len(result["verification_history"]) == 2
    state["drafts"][0].title = "Requests stalled."
    assert run(state)["verification_results"][0].decision == "pass"


def test_zero_retry_budget_immediately_holds_repairable_failures(state):
    result = run(state, llm=True, model=Verifier("unsupported"), max_retries=0)
    assert "RETRY_LIMIT_REACHED" in codes(result) and not result["retry_requests"]


def test_rewrite_receives_feedback_and_clears_old_admission_but_keeps_history(state):
    state.update(run(state, llm=True, model=Verifier("unsupported")))
    state["verified_drafts"] = [state["drafts"][0].model_copy(deep=True)]
    writer = Writer()
    state.update(asyncio.run(draft_state(state, writer)))
    assert writer.calls[0]["retry_feedback"][0]["issues"][0]["code"] == "UNSUPPORTED_CLAIM"
    assert not state["verified_drafts"] and not state["verification_results"]
    assert len(state["verification_history"]) == 1
    assert state["extraction_result"].experiences


def test_mixed_batch_only_admits_passing_candidates(state):
    source = state["extraction_result"].experiences[0].model_copy(deep=True)
    source.problem = source.rationale.model_copy(deep=True)
    state["extraction_result"].experiences.append(source)
    state.update(asyncio.run(draft_state(state, Writer())))
    state["drafts"][1].title = "Another unsupported claim"
    result = run(state)
    assert [item.decision for item in result["verification_results"]] == ["pass", "needs_review"]
    assert len(result["verified_drafts"]) == 1 and len(result["review_drafts"]) == 1


def test_verification_contract_cannot_claim_pass_with_errors():
    with pytest.raises(ValidationError):
        VerificationResult(experience_id="candidate", decision="retry")
    with pytest.raises(ValidationError):
        VerificationOptions(rules_enabled=False)


def test_candidate_key_still_matches_original_extraction_after_gate(state):
    run(state)
    assert state["drafts"][0].experience_id == experience_id(
        state["extraction_result"].experiences[0]
    )


def test_generalized_lesson_keeps_confirmed_experience_evidence_for_gate(state):
    class GeneralizingWriter(Writer):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            result.draft.lesson = "Check dependency health before retrying requests."
            return result

    update = asyncio.run(draft_state(state, GeneralizingWriter()))
    draft = update["drafts"][0]
    source = draft.source_extraction
    refs = set(draft.field_evidence_refs["lesson"])
    assert set(source.approach.evidence_refs + source.outcome.evidence_refs) <= refs
    assert draft.lesson_status == "inferred"
    state.update(update)
    assert run(state)["verification_results"][0].decision == "needs_review"


def test_duplicate_candidate_keys_cannot_produce_duplicate_admissions(state):
    state["drafts"].append(state["drafts"][0].model_copy(deep=True))
    result = run(state, llm=True, model=forbidden_generator())
    assert not result["verified_drafts"]
    assert all(item.decision == "needs_review" for item in result["verification_results"])
    assert "DUPLICATE_CANDIDATE" in codes(result)


def test_unqualified_title_cannot_promote_a_retained_hypothesis(state):
    fact = state["extraction_result"].experiences[0].cause.model_dump()
    fact["status"] = "hypothesis"
    redraft(state, cause=fact)
    state["drafts"][0].title = fact["value"]
    result = run(state, llm=True, model=forbidden_generator())
    assert "UNCERTAINTY_PROMOTED" in codes(result)
    assert result["verification_results"][0].retry_node == "draft_card"


def test_literal_matching_does_not_detach_uncertainty_from_adjacent_text(state):
    document = state["documents"][0]
    # A whole block explicitly qualifies the cause on a preceding line.
    document.blocks[2].text = "Hypothesis:\nDatabase was unavailable."
    ref = document.evidence_index[document.blocks[2].evidence_id]
    ref.excerpt = document.blocks[2].text
    ref.end_char = len(ref.excerpt)
    source = state["extraction_result"].experiences[0]
    source.evidence_index[document.blocks[2].evidence_id] = ref.model_copy(deep=True)
    redraft(state)
    result = run(state)
    assert "SEMANTIC_CHECK_REQUIRED" in codes(result)
    assert result["verification_results"][0].decision == "needs_review"


@pytest.mark.parametrize(
    "failure_kind,target", [("context_missing", "chunk"), ("source_unreadable", "parse")]
)
def test_semantic_failure_kind_selects_the_repair_boundary(state, failure_kind, target):
    result = run(state, llm=True, model=Verifier("uncertain", failure_kind=failure_kind))
    assert result["verification_results"][0].retry_node == target


def test_same_experience_can_be_rewritten_then_pass_after_source_based_semantic_check(state):
    from app.domains.documents.pipeline.nodes.draft_card import make_draft_node

    state["drafts"][0].title = "Unsupported causal conclusion"
    first = run(state, llm=True, model=Verifier("unsupported"))
    state.update(first)
    state.update(asyncio.run(make_draft_node(Writer("Requests stalled."))(state)))
    assert run(state, llm=True, model=Verifier())["verification_results"][0].decision == "pass"


def test_real_file_to_gate_preserves_evidence_and_resets_stale_admission(state, tmp_path):
    from app.domains.documents.pipeline.document import DocumentSource
    from app.domains.documents.pipeline.nodes.chunk import chunk
    from app.domains.documents.pipeline.nodes.extract import make_extract_node
    from app.domains.documents.pipeline.nodes.normalize import normalize
    from app.domains.documents.pipeline.nodes.parse import parse

    values = [
        getattr(state["extraction_result"].experiences[0], name).value for name in FACT_FIELDS
    ]
    path = tmp_path / "original.md"
    path.write_bytes("\n\n".join(values).encode())
    state.update(run(state, llm=True, model=Verifier("unsupported")))
    old_history = deepcopy(state["verification_history"])
    state["verified_drafts"] = deepcopy(state["drafts"])
    state["sources"] = [
        DocumentSource(document_id=DOC_ID, original_filename="original.md", path=path)
    ]
    state.update(asyncio.run(parse(state)))
    assert not state["verified_drafts"] and not state["verification_results"]
    state.update(normalize(state))
    state.update(chunk(state))

    class Extractor:
        async def generate(self, *, instructions, payload, output_type):
            refs = {block["text"].strip(): block["evidence_id"] for block in payload["blocks"]}
            return output_type.model_validate(
                {
                    "experiences": [
                        {
                            **{
                                name: {
                                    "value": value,
                                    "status": "confirmed",
                                    "evidence_refs": [refs[value]],
                                }
                                for name, value in zip(FACT_FIELDS, values, strict=True)
                            },
                            "technologies": ["FastAPI"],
                            "keywords": [],
                        }
                    ]
                }
            )

    state.update(asyncio.run(make_extract_node(Extractor())(state)))
    state.update(asyncio.run(draft_state(state, Writer())))
    result = run(state)
    assert result["verification_results"][0].decision == "pass"
    assert result["verification_history"] == old_history
    source = result["verified_drafts"][0].source_extraction
    ref = source.evidence_index[source.problem.evidence_refs[0]]
    block = next(block for block in state["documents"][0].blocks if block.block_id == ref.block_id)
    assert block.text[ref.start_char : ref.end_char] == ref.excerpt
    assert state["documents"][0].original_filename == "original.md"
