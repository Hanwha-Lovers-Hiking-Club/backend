from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import CamelModel

JobStatus = Literal["queued", "running", "completed", "partial", "failed"]


class UploadedDocument(CamelModel):
    document_id: UUID
    original_filename: str
    size_bytes: int


class UploadResponse(CamelModel):
    job_id: UUID
    status: JobStatus
    documents: list[UploadedDocument]


class DocumentResult(CamelModel):
    document_id: UUID
    status: Literal["failed", "incomplete", "needs_review", "ready_to_save", "completed"]
    successful_chunks: int
    failed_chunks: int
    admitted_cards: int
    review_cards: int
    rejected_cards: int
    skipped_experiences: int
    saved_card_ids: list[UUID]
    payload_hash: str | None


class JobFailure(CamelModel):
    document_id: UUID
    stage: str
    code: str
    chunk_id: str | None = None
    experience_id: str | None = None


class JobResult(CamelModel):
    documents: list[DocumentResult]
    total_documents: int
    draft_cards: int
    admitted_cards: int
    review_cards: int
    rejected_cards: int
    skipped_experiences: int
    saved_cards: int
    failures: list[JobFailure]
    error_code: str | None = None


class JobResponse(CamelModel):
    job_id: UUID = Field(validation_alias="id")
    status: JobStatus
    document_ids: list[UUID]
    result: JobResult | None = None
    created_at: str
    updated_at: str
