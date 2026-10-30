"""Internal gate decisions, claim checks and bounded retry contracts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.documents.pipeline.models import EvidenceId, KnowledgeCardDraft, NonBlankText

RetryNode = Literal["parse", "normalize", "chunk", "extract", "draft_card"]
Decision = Literal["pass", "retry", "reject", "needs_review"]


class GateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerificationOptions(GateModel):
    rules_enabled: Literal[True] = True
    llm_enabled: bool = False
    max_retries: int = Field(default=2, ge=0, le=5)
    max_input_chars: int = Field(default=48000, gt=0)


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

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if self.verdict == "supported" and self.failure_kind is not None:
            raise ValueError("supported claims cannot also report a failure")
        return self


class SemanticReport(GateModel):
    assessments: list[ClaimAssessment]
