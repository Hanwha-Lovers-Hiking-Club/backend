# 내부 Gate 판정, 주장 검증, 횟수가 제한된 재처리 요청의 데이터 모델을 정의한다.

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.documents.pipeline.execution_budget import MAX_GATE_RETRIES
from app.domains.documents.pipeline.models import EvidenceId, KnowledgeCardDraft, NonBlankText

RetryNode = Literal["parse", "normalize", "chunk", "extract", "draft_card"]
Decision = Literal["pass", "retry", "reject", "needs_review"]


class GateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerificationOptions(GateModel):
    rules_enabled: Literal[True] = True
    llm_enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=MAX_GATE_RETRIES)
    max_input_chars: int = Field(default=48000, gt=0)
    # 추가 원문 문맥의 직렬화 문자 상한이다. 전체 요청 상한도 별도로 지킨다.
    context_max_chars: int = Field(default=12000, ge=0)


class VerificationIssue(GateModel):
    check: Literal["structure", "minimum", "evidence", "consistency", "retry"]
    code: NonBlankText
    field: NonBlankText
    reason: NonBlankText
    action: NonBlankText
    disposition: Literal["retry", "reject", "needs_review"]
    retry_node: RetryNode | None = None

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if (self.disposition == "retry") != (self.retry_node is not None):
            raise ValueError("only retry issues select a target node")
        return self


class VerificationResult(GateModel):
    experience_id: NonBlankText
    decision: Decision
    issues: list[VerificationIssue] = Field(default_factory=list)
    retry_node: RetryNode | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if (self.decision == "pass") != (not self.issues):
            raise ValueError("only a passing result has no issues")
        if (self.decision == "retry") != (self.retry_node is not None):
            raise ValueError("only retry decisions select a target node")
        return self


class RetryRequest(GateModel):
    experience_id: NonBlankText
    target_node: RetryNode
    input_fingerprint: NonBlankText
    issues: list[VerificationIssue] = Field(min_length=1)


class Claim(GateModel):
    key: NonBlankText
    field: NonBlankText
    value: NonBlankText
    status: Literal["confirmed", "hypothesis", "inferred", "unknown", "conflicted"]
    evidence_refs: list[EvidenceId] = Field(min_length=1)


class RetryWork(GateModel):
    request: RetryRequest
    draft: KnowledgeCardDraft


class ClaimAssessment(GateModel):
    key: NonBlankText
    verdict: Literal["supported", "unsupported", "uncertain", "conflicted"]
    evidence_refs: list[EvidenceId] = Field(min_length=1)
    reason: NonBlankText
    failure_kind: Literal["claim", "context_missing", "source_unreadable"] | None = None
    requires_document_scope: bool = Field(default=False, strict=True)
    counter_evidence_refs: list[EvidenceId] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if self.verdict == "supported" and (
            self.failure_kind is not None or self.counter_evidence_refs
        ):
            raise ValueError("supported claims cannot also report a failure")
        return self


class SemanticReport(GateModel):
    assessments: list[ClaimAssessment]


class VerificationTrace(GateModel):
    """의미 검증의 성공 판정까지 보존한다. 원문 사본이나 클라이언트는 넣지 않는다."""

    experience_id: NonBlankText
    input_fingerprint: NonBlankText
    supporting_evidence_ids: list[EvidenceId]
    context_block_ids: list[NonBlankText]
    document_coverage: dict[str, Literal["complete", "partial", "unavailable"]]
    input_chars: int
    input_sha256: NonBlankText
    assessments: list[ClaimAssessment] = Field(default_factory=list)
    report_valid: bool = False
    error_code: str | None = None
