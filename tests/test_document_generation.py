import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domains.documents.pipeline.chunking import chunk_document
from app.domains.documents.pipeline.document import ParsedBlock, ParsedDocument
from app.domains.documents.pipeline.extraction import experience_id
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.models import (
    DraftProposal,
    ExtractedElements,
    ExtractionResult,
    KnowledgeCardDraft,
)
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.normalization import normalize_document
from app.domains.knowledge_cards.schemas import KnowledgeCard

DOC_ID = "4c10a229-9b3a-40bc-8611-e2f787540f03"


class FakeGenerator:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, *, instructions, payload, output_type):
        self.calls.append((instructions, deepcopy(payload), output_type))
        assert self.responses, "Unexpected model request"
        return output_type.model_validate(self.responses.pop(0))


@pytest.fixture
def source_state():
    document = normalize_document(
        ParsedDocument(
            document_id=DOC_ID,
            original_filename="original name.md",
            blocks=[
                ParsedBlock(
                    block_type="paragraph",
                    location="lines 1-4",
                    text=(
                        "FastAPI service. Requests stalled. Might be Redis. Added metrics. "
                        "Latency decreased. Measure before changing. Another independent problem."
                    ),
                )
            ],
        )
    )
    return {"documents": [document], "chunks": chunk_document(document)}


def candidate(state, *, problem="Requests stalled."):
    ref = state["documents"][0].blocks[0].evidence_id

    def fact(value, status="confirmed"):
        return {"value": value, "status": status, "evidence_refs": [ref]}

    return {
        "context": fact("FastAPI service."),
        "problem": fact(problem),
        "cause": fact("Might be Redis.", "hypothesis"),
        "approach": fact("Added metrics."),
        "outcome": fact("Latency decreased."),
        "lesson": fact("Measure before changing.", "inferred"),
        "technologies": ["FastAPI"],
        "keywords": ["latency"],
    }


def bound(state, **changes):
    data = candidate(state)
    data.update(changes)
    return ExtractedElements(**data, evidence_index=state["documents"][0].evidence_index)


def written(title="Measure the stalled requests"):
    return {
        "draft": {
            "title": title,
            "summary": "Measurements informed the response.",
            "lesson": "Measure before changing.",
            "keywords": ["latency", "measurement"],
        },
        "skip_reason": None,
    }


def test_extract_returns_multiple_independent_experiences_with_owned_evidence(source_state):
    model = FakeGenerator(
        {
            "experiences": [
                candidate(source_state),
                candidate(source_state, problem="Another independent problem."),
            ]
        }
    )
    before = deepcopy(source_state)
    update = asyncio.run(make_extract_node(model)(source_state))
    first, second = update["extraction_result"].experiences
    assert first.problem.value != second.problem.value
    assert first.cause.status == "hypothesis"
    assert experience_id(first) != experience_id(second)
    ref = first.problem.evidence_refs[0]
    assert first.evidence_index[ref] == source_state["documents"][0].evidence_index[ref]
    assert first.evidence_index[ref] is not source_state["documents"][0].evidence_index[ref]
    assert source_state == before
    assert model.calls[0][1]["original_filename"] == "original name.md"


def test_extract_zero_experiences_and_empty_chunks_do_not_create_cards(source_state):
    model = FakeGenerator({"experiences": []})
    result = asyncio.run(make_extract_node(model)(source_state))
    assert result["extraction_result"].experiences == []
    no_model = FakeGenerator()
    assert asyncio.run(make_extract_node(no_model)({"documents": [], "chunks": []}))["drafts"] == []
    assert no_model.calls == []


def test_duplicate_chunks_and_identical_proposals_are_not_duplicated(source_state):
    source_state["chunks"] *= 2
    data = candidate(source_state)
    model = FakeGenerator({"experiences": [data, data]})
    result = asyncio.run(make_extract_node(model)(source_state))
    assert len(result["extraction_result"].experiences) == 1
    assert len(model.calls) == 1


def test_unknown_evidence_id_is_rejected_instead_of_creating_a_source(source_state):
    data = candidate(source_state)
    data["cause"]["evidence_refs"] = ["invented"]
    with pytest.raises(GenerationError) as exc:
        asyncio.run(make_extract_node(FakeGenerator({"experiences": [data]}))(source_state))
    assert exc.value.code == "UNKNOWN_EVIDENCE"


def test_changed_chunk_source_is_rejected_before_model_call(source_state):
    source_state["chunks"][0] = source_state["chunks"][0].model_copy(deep=True)
    source_state["chunks"][0].blocks[0].text = "Fabricated block"
    model = FakeGenerator()
    with pytest.raises(GenerationError) as exc:
        asyncio.run(make_extract_node(model)(source_state))
    assert exc.value.code == "INVALID_CHUNK_SOURCE"
    assert model.calls == []


def test_large_input_is_not_silently_truncated(source_state):
    model = FakeGenerator()
    with pytest.raises(GenerationError) as exc:
        asyncio.run(make_extract_node(model, max_input_chars=10)(source_state))
    assert exc.value.code == "INPUT_TOO_LARGE"
    assert model.calls == []


def test_draft_keeps_fact_status_and_independent_snapshots_for_verification(source_state):
    first = bound(source_state)
    second = bound(
        source_state, problem={**candidate(source_state)["problem"], "value": "Other issue"}
    )
    extraction = ExtractionResult(experiences=[first, second])
    source_state["extraction_result"] = extraction
    model = FakeGenerator(written(), written("Second experience"))
    update = asyncio.run(make_draft_node(model)(source_state))
    drafts = update["drafts"]
    assert len(drafts) == 2 and update["draft_skips"] == []
    assert all(draft.status == "draft" and draft.cause is None for draft in drafts)
    assert drafts[0].source_extraction.cause.status == "hypothesis"
    assert drafts[0].source_extraction.cause.value == "Might be Redis."
    assert drafts[0].keywords == ["latency", "measurement"]
    assert drafts[0].technologies == ["FastAPI"]
    assert source_state["extraction_result"] is extraction
    first.cause.value = "Changed later"
    assert drafts[0].source_extraction.cause.value == "Might be Redis."
    assert drafts[1].source_extraction.cause.value == "Might be Redis."


@pytest.mark.parametrize("status", ["unknown", "hypothesis", "inferred", "conflicted"])
def test_unconfirmed_cause_is_never_promoted_to_a_factual_card_field(source_state, status):
    fact = candidate(source_state)["cause"]
    fact["status"] = status
    if status in {"unknown", "conflicted"}:
        fact["value"] = None
    item = bound(source_state, cause=fact)
    result = asyncio.run(
        make_draft_node(FakeGenerator(written()))(
            {"extraction_result": ExtractionResult(experiences=[item])}
        )
    )
    draft = result["drafts"][0]
    assert draft.cause is None
    assert draft.source_extraction.cause.status == status
    tampered = draft.model_dump()
    tampered["cause"] = "Redis caused the incident"
    with pytest.raises(ValidationError):
        KnowledgeCardDraft.model_validate(tampered)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"context": {}}, "NO_CONFIRMED_CONTEXT"),
        (
            {"problem": {}, "cause": {}, "approach": {}, "rationale": {}, "outcome": {}},
            "NO_EXPERIENCE_FACTS",
        ),
        ({"lesson": {}, "outcome": {}}, "NO_SUPPORTED_LESSON"),
    ],
)
def test_insufficient_information_skips_draft_without_paid_request(source_state, changes, code):
    model = FakeGenerator()
    result = asyncio.run(
        make_draft_node(model)(
            {"extraction_result": ExtractionResult(experiences=[bound(source_state, **changes)])}
        )
    )
    assert result["drafts"] == []
    assert result["draft_skips"][0].code == code
    assert result["draft_skips"][0].source_extraction.evidence_index
    assert model.calls == []


def test_lesson_can_be_derived_from_confirmed_approach_and_outcome(source_state):
    item = bound(source_state, lesson={})
    draft = asyncio.run(
        make_draft_node(FakeGenerator(written()))(
            {"extraction_result": ExtractionResult(experiences=[item])}
        )
    )["drafts"][0]
    assert draft.source_extraction.lesson.status == "unknown"
    assert draft.lesson_status == "inferred"
    assert draft.lesson == "Measure before changing."
    assert set(draft.field_evidence_refs["lesson"]) == set(
        item.approach.evidence_refs + item.outcome.evidence_refs
    )


def test_model_can_decline_a_draft_with_reason(source_state):
    result = asyncio.run(
        make_draft_node(FakeGenerator({"draft": None, "skip_reason": "Insufficient detail"}))(
            {"extraction_result": ExtractionResult(experiences=[bound(source_state)])}
        )
    )
    assert result["drafts"] == []
    assert result["draft_skips"][0].code == "MODEL_DECLINED"


def test_draft_rerun_replaces_results_with_reproducible_candidate_keys(source_state):
    source_state["extraction_result"] = ExtractionResult(experiences=[bound(source_state)])
    node = make_draft_node(FakeGenerator(written(), written()))
    source_state.update(asyncio.run(node(source_state)))
    key = source_state["drafts"][0].experience_id
    source_state.update(asyncio.run(node(source_state)))
    assert len(source_state["drafts"]) == 1 and source_state["drafts"][0].experience_id == key


def test_draft_body_uses_existing_api_fields_without_private_metadata_or_fake_scores(source_state):
    draft = asyncio.run(
        make_draft_node(FakeGenerator(written()))(
            {"extraction_result": ExtractionResult(experiences=[bound(source_state)])}
        )
    )["drafts"][0]
    body = draft.to_card_fields()
    assert set(body) <= KnowledgeCard.model_fields.keys()
    assert "source_extraction" not in body and "quality_score" not in body
    now = datetime.now(timezone.utc)
    card = KnowledgeCard.model_validate(
        {
            **body,
            "id": DOC_ID,
            "created_at": now,
            "updated_at": now,
            "evidences": [],
            "project_name": None,
            "department": None,
            "project_date": None,
            "quality_score": None,
            "last_verified_at": None,
            "verification_version": None,
        }
    )
    assert card.model_dump(mode="json", by_alias=True)["cause"] is None
    assert card.status == "draft"


@pytest.mark.parametrize(
    "payload", [{}, {"draft": None}, {"draft": written()["draft"], "skip_reason": "Both"}]
)
def test_draft_proposal_must_return_one_outcome(payload):
    with pytest.raises(ValidationError):
        DraftProposal.model_validate(payload)


def test_parse_to_draft_preserves_original_location_and_uncertain_cause(tmp_path):
    from app.domains.documents.pipeline.document import DocumentSource
    from app.domains.documents.pipeline.models import ExtractionProposal
    from app.domains.documents.pipeline.nodes.chunk import chunk
    from app.domains.documents.pipeline.nodes.normalize import normalize
    from app.domains.documents.pipeline.nodes.parse import parse

    path = tmp_path / "source.md"
    path.write_text(
        "# Incident\n\nFastAPI service. Requests stalled. Might be Redis. Added metrics. "
        "Latency decreased. Measure before changing.\n",
        encoding="utf-8",
    )

    class Model:
        async def generate(self, *, instructions, payload, output_type):
            if output_type is ExtractionProposal:
                block = next(b for b in payload["blocks"] if b["block_type"] == "paragraph")
                data = {
                    name: {
                        "value": value,
                        "status": status,
                        "evidence_refs": [block["evidence_id"]],
                    }
                    for name, value, status in [
                        ("context", "FastAPI service.", "confirmed"),
                        ("problem", "Requests stalled.", "confirmed"),
                        ("cause", "Might be Redis.", "hypothesis"),
                        ("approach", "Added metrics.", "confirmed"),
                        ("outcome", "Latency decreased.", "confirmed"),
                        ("lesson", "Measure before changing.", "inferred"),
                    ]
                }
                return output_type.model_validate({"experiences": [data]})
            return output_type.model_validate(written())

    async def run():
        state = {
            "sources": [
                DocumentSource(document_id=DOC_ID, original_filename="Original.md", path=path)
            ]
        }
        state.update(await parse(state))
        state.update(normalize(state))
        state.update(chunk(state))
        state.update(await make_extract_node(Model())(state))
        state.update(await make_draft_node(Model())(state))
        return state

    state = asyncio.run(run())
    draft = state["drafts"][0]
    ref = draft.source_extraction.evidence_index[draft.source_extraction.cause.evidence_refs[0]]
    assert ref.location == "lines 3-3"
    # Preserve actual CRLF on Windows; read_text() would normalize it to LF.
    raw_lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    assert ref.excerpt == "".join(raw_lines[2:])
    assert draft.cause is None and draft.source_extraction.cause.status == "hypothesis"
    assert state["documents"][0].original_filename == "Original.md"
    assert state["extraction_result"].experiences[0] == draft.source_extraction
