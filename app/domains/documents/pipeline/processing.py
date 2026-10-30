# 문서별 실패·완료 상태와 청크별 재시도 기록, 실제 저장 확인 계약을 정의한다.

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.documents.pipeline.document import DocumentIR
from app.domains.documents.pipeline.embeddings.models import CardEmbeddings
from app.domains.documents.pipeline.models import DraftSkip, ExtractionResult, KnowledgeCardDraft
from app.domains.documents.pipeline.verification.models import VerificationResult


class ProcessingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProcessingOptions(ProcessingModel):
    # 의미 검증 재작성 예산과 별개인 일시적 호출 실패 예산이다.
    max_chunk_attempts: int = Field(default=3, ge=1, le=5)
    retry_delay_seconds: float = Field(default=1, ge=0, le=10)


class ProcessingFailure(ProcessingModel):
    document_id: UUID
    stage: Literal["parse", "extract", "draft_card", "embed", "save"]
    code: str
    chunk_id: str | None = None
    experience_id: str | None = None


class ChunkProgress(ProcessingModel):
    document_id: UUID
    chunk_id: str
    fingerprint: str
    status: Literal["pending", "succeeded", "failed"] = "pending"
    attempts: int = 0
    error_code: str | None = None
    result: ExtractionResult = Field(default_factory=ExtractionResult)


class DocumentProgress(ProcessingModel):
    document_id: UUID
    status: Literal["failed", "incomplete", "needs_review", "ready_to_save", "completed"]
    successful_chunks: int = 0
    failed_chunks: int = 0
    admitted_cards: int = 0
    review_cards: int = 0
    rejected_cards: int = 0
    skipped_experiences: int = 0
    saved_card_ids: list[UUID] = Field(default_factory=list)
    payload_hash: str | None = None


class SaveReceipt(ProcessingModel):
    # 카드·근거·필요한 벡터와 문서 완료 기록을 영속 저장한 어댑터만 반환한다.
    # 일부만 저장했거나 fake 저장인 경우 이 계약을 만족하지 않는다.
    document_id: UUID
    payload_hash: str
    committed: Literal[True]
    result_recorded: Literal[True]
    card_ids: list[UUID]


class DocumentSaveRequest(ProcessingModel):
    # 카드에 보존된 필드별 근거와 문서 IR을 함께 넘긴다. 다른 문서의 작업 상태는 포함하지 않는다.
    document: DocumentIR
    cards: list[KnowledgeCardDraft]
    embeddings: list[CardEmbeddings]
    verification_results: list[VerificationResult]
    skips: list[DraftSkip]
    payload_hash: str
