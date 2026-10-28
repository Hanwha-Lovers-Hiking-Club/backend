# LLM 구조화 출력 모델 (ExtractedElements, CardDraft, VerificationResult 등).
# 노드 간 경계에서 오가는 데이터 모양이므로 state.py와 함께 팀이 합의한 계약이다.

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# 사실의 상태다. KnowledgeCard의 draft/active/flagged/archived와 별개다.
FactStatus = Literal["confirmed", "hypothesis", "inferred", "unknown", "conflicted"]


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must contain a non-whitespace character")
    # 인용문과 문자 구간이 달라지지 않도록 strip/정규화하지 않는다.
    return value


NonBlankText = Annotated[str, Field(strict=True, min_length=1), AfterValidator(_require_non_blank)]
EvidenceId = NonBlankText


def _explicit_status_schema(schema: dict) -> None:
    # Python can default to unknown; the LLM wire schema asks for an explicit status.
    schema.pop("default", None)


class EvidenceRef(BaseModel):
    """원문 블록의 연속된 인용 구간. 문자열을 읽거나 바꾸는 파서가 아니다.

    start_char/end_char는 원문을 보존한 블록 text의 Python 문자 인덱스다.
    0부터 시작하고 끝은 포함하지 않는다: text[start_char:end_char] == excerpt.
    위치는 선택적이다. 둘 다 생략하거나 둘 다 제공하며 파서/정규화 코드가 계산한다.
    블록 존재 여부와 실제 문자열 일치는 6번 Verification Gate에서 검사한다.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    block_id: NonBlankText
    location: NonBlankText
    excerpt: NonBlankText
    start_char: int | None = Field(default=None, ge=0, strict=True)
    end_char: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_quote_span(self) -> Self:
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must be supplied together")
        if self.start_char is None:
            return self
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        if self.end_char - self.start_char != len(self.excerpt):
            raise ValueError("quote span length must match excerpt length")
        return self


class ExtractedField(BaseModel):
    """추출값과 사실 상태, 그 판단에 사용한 원문 근거를 함께 보존한다.

    hypothesis는 원문에 명시된 가설, inferred는 근거에서 도출한 추론이다.
    conflicted는 결론을 정하지 못해 value=None일 수 있다. 충돌 근거는 보존한다.
    이 모델의 통과는 해당 값의 사실성이 검증됐다는 뜻이 아니다.
    """

    model_config = ConfigDict(extra="forbid")

    value: NonBlankText | None = None
    status: FactStatus = Field(default="unknown", json_schema_extra=_explicit_status_schema)
    evidence_refs: list[EvidenceId] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fact_metadata(self) -> Self:
        if self.status == "unknown":
            if self.value is not None:
                raise ValueError("unknown fields must have value=None")
            return self
        if self.status != "conflicted" and self.value is None:
            raise ValueError("known, hypothesized or inferred fields must have a value")
        if not self.evidence_refs:
            raise ValueError("non-unknown fields must include evidence_refs")
        return self


class ExtractedExperienceFields(BaseModel):
    """LLM output fields only. The model never generates source records or coordinates."""

    model_config = ConfigDict(extra="forbid")

    context: ExtractedField = Field(default_factory=ExtractedField)
    problem: ExtractedField = Field(default_factory=ExtractedField)
    cause: ExtractedField = Field(default_factory=ExtractedField)
    approach: ExtractedField = Field(default_factory=ExtractedField)
    rationale: ExtractedField = Field(default_factory=ExtractedField)
    outcome: ExtractedField = Field(default_factory=ExtractedField)
    lesson: ExtractedField = Field(default_factory=ExtractedField)
    technologies: list[NonBlankText] = Field(default_factory=list)
    keywords: list[NonBlankText] = Field(default_factory=list)


class ExtractedElements(ExtractedExperienceFields):
    """One independent experience with its code-owned evidence registry."""

    evidence_index: dict[EvidenceId, EvidenceRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> Self:
        for name in FACT_FIELDS:
            fact = getattr(self, name)
            missing = set(fact.evidence_refs) - self.evidence_index.keys()
            if missing:
                raise ValueError(f"{name}: unknown evidence IDs: {sorted(missing)}")
        return self


FACT_FIELDS = ("context", "problem", "cause", "approach", "rationale", "outcome", "lesson")


class ExtractionResult(BaseModel):
    """Zero to many experiences. Each retains its own statuses and evidence registry."""

    model_config = ConfigDict(extra="forbid")
    experiences: list[ExtractedElements] = Field(default_factory=list)


class ExtractionProposal(BaseModel):
    """Structured LLM response. Evidence registry is attached and validated by code."""

    model_config = ConfigDict(extra="forbid")
    experiences: list[ExtractedExperienceFields] = Field(default_factory=list)


class DraftText(BaseModel):
    """Only fields which require writing. Factual body fields are copied by code."""

    model_config = ConfigDict(extra="forbid")
    title: NonBlankText
    summary: NonBlankText
    lesson: NonBlankText
    keywords: list[NonBlankText] = Field(default_factory=list)


class DraftProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: DraftText | None = None
    skip_reason: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if (self.draft is None) == (self.skip_reason is None):
            raise ValueError("provide either draft or skip_reason")
        return self


class KnowledgeCardDraft(BaseModel):
    """Unverified card plus the immutable-by-convention extraction snapshot for the gate.

    experience_id is an internal reproducible candidate key, not a saved card row ID.
    No quality evaluation or activation is performed at this stage.
    """

    model_config = ConfigDict(extra="forbid")
    experience_id: NonBlankText
    source_extraction: ExtractedElements
    field_evidence_refs: dict[NonBlankText, list[EvidenceId]]
    lesson_status: Literal["confirmed", "inferred"]
    title: NonBlankText
    summary: NonBlankText
    context: NonBlankText
    problem: NonBlankText | None = None
    cause: NonBlankText | None = None
    approach: NonBlankText | None = None
    rationale: NonBlankText | None = None
    outcome: NonBlankText | None = None
    lesson: NonBlankText
    technologies: list[NonBlankText] = Field(default_factory=list)
    keywords: list[NonBlankText] = Field(default_factory=list)
    status: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def validate_source_contract(self) -> Self:
        expected_fields = {"title", "summary", *FACT_FIELDS}
        if self.field_evidence_refs.keys() != expected_fields:
            raise ValueError("draft must retain evidence links for every narrative field")
        if self.technologies != self.source_extraction.technologies:
            raise ValueError("draft must preserve extracted technologies")
        expected_lesson_status = (
            "confirmed"
            if self.source_extraction.lesson.status == "confirmed"
            and self.lesson == self.source_extraction.lesson.value
            else "inferred"
        )
        if self.lesson_status != expected_lesson_status:
            raise ValueError("a derived or rewritten lesson must remain inferred")
        for name in FACT_FIELDS[:-1]:
            fact = getattr(self.source_extraction, name)
            expected = fact.value if fact.status == "confirmed" else None
            if getattr(self, name) != expected:
                raise ValueError(f"{name}: draft must preserve confirmed source values only")
        for name, refs in self.field_evidence_refs.items():
            if getattr(self, name) is not None and not refs:
                raise ValueError(f"{name}: draft text requires source evidence")
            if set(refs) - self.source_extraction.evidence_index.keys():
                raise ValueError("draft references unknown evidence")
        return self

    def to_card_fields(self) -> dict:
        """Body fields with existing API/DB names; no synthetic IDs, dates or scores."""
        return self.model_dump(
            mode="json",
            exclude={"experience_id", "source_extraction", "field_evidence_refs", "lesson_status"},
        )


class DraftSkip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_id: NonBlankText
    code: NonBlankText
    reason: NonBlankText
    source_extraction: ExtractedElements
