from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domains.documents.pipeline.models import EvidenceRef, ExtractedElements, ExtractionResult

DOCUMENT_ID = "4c10a229-9b3a-40bc-8611-e2f787540f03"


def evidence(**changes):
    return {
        "document_id": DOCUMENT_ID,
        "block_id": "block-1",
        "location": "p.1",
        "excerpt": "  API latency\n",
        **changes,
    }


def experience():
    return {
        "problem": {"value": "API latency", "status": "confirmed", "evidence_refs": ["ev-1"]},
        "cause": {"value": "Possibly the query", "status": "hypothesis", "evidence_refs": ["ev-1"]},
        "technologies": ["FastAPI"],
        "keywords": ["latency"],
        "evidence_index": {"ev-1": evidence()},
    }


def test_design_payload_shared_evidence_and_metadata_survive_round_trip():
    payload = experience()
    before = deepcopy(payload)
    result = ExtractedElements.model_validate(payload)
    restored = ExtractedElements.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.problem.evidence_refs == restored.cause.evidence_refs == ["ev-1"]
    assert restored.cause.status == "hypothesis"
    assert restored.evidence_index["ev-1"].excerpt == "  API latency\n"
    assert restored.evidence_index["ev-1"].start_char is None
    assert restored.technologies == ["FastAPI"]
    assert payload == before


@pytest.mark.parametrize("field", ["problem", "cause", "lesson"])
def test_missing_evidence_id_is_rejected_even_for_unknown_fields(field):
    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        ExtractedElements.model_validate({field: {"evidence_refs": ["missing"]}})


@pytest.mark.parametrize("refs", [[""], ["  "], [123], [evidence()]])
def test_evidence_refs_are_nonblank_string_ids(refs):
    with pytest.raises(ValidationError):
        ExtractedElements.model_validate({"cause": {"evidence_refs": refs}})


@pytest.mark.parametrize("field", ["technologies", "keywords"])
@pytest.mark.parametrize("value", ["FastAPI", [123], [" "], {"value": ["FastAPI"]}])
def test_tags_are_plain_string_arrays(field, value):
    with pytest.raises(ValidationError):
        ExtractedElements.model_validate({field: value})


def test_empty_extraction_does_not_invent_information():
    item = ExtractedElements()
    assert item.cause.value is None and item.cause.status == "unknown"
    assert item.technologies == item.keywords == []
    assert item.evidence_index == {}


@pytest.mark.parametrize("count", [0, 1, 3])
def test_zero_to_many_independent_experiences_keep_individual_provenance(count):
    result = ExtractionResult.model_validate({"experiences": [experience() for _ in range(count)]})
    assert len(result.experiences) == count
    if count > 1:
        result.experiences[0].keywords.append("changed")
        assert result.experiences[1].keywords == ["latency"]
        assert result.experiences[1].cause.status == "hypothesis"


@pytest.mark.parametrize(
    "changes",
    [
        {"start_char": 0},
        {"end_char": 14},
        {"start_char": -1, "end_char": 13},
        {"start_char": True, "end_char": 14},
        {"start_char": 1, "end_char": 1},
        {"start_char": 0, "end_char": 100},
        {"document_id": "bad"},
        {"block_id": " "},
        {"location": " "},
    ],
)
def test_optional_quote_positions_are_validated_when_present(changes):
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate(evidence(**changes))


def test_valid_optional_span_preserves_whitespace():
    item = EvidenceRef.model_validate(
        evidence(start_char=5, end_char=5 + len(evidence()["excerpt"]))
    )
    assert item.start_char == 5
    assert item.excerpt == "  API latency\n"


@pytest.mark.parametrize(
    "fact",
    [
        {"value": "fact", "status": "unknown"},
        {"value": "fact", "status": "confirmed"},
        {"status": "confirmed", "evidence_refs": ["ev-1"]},
        {"value": "fact", "status": "active", "evidence_refs": ["ev-1"]},
    ],
)
def test_invalid_fact_metadata_is_rejected(fact):
    with pytest.raises(ValidationError):
        ExtractedElements.model_validate({"cause": fact, "evidence_index": {"ev-1": evidence()}})


def test_unresolved_conflicts_preserve_both_sources():
    item = ExtractedElements.model_validate(
        {
            "cause": {"status": "conflicted", "evidence_refs": ["a", "b"]},
            "evidence_index": {
                "a": evidence(),
                "b": evidence(block_id="block-2", excerpt="Another cause"),
            },
        }
    )
    assert item.cause.value is None
    assert item.cause.evidence_refs == ["a", "b"]
    assert item.evidence_index["b"].block_id == "block-2"
