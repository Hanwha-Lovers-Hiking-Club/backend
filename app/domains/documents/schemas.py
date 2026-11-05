# 문서 API의 요청/응답 모양(pydantic 모델)만 정의한다.
# 파이프라인 내부에서 쓰는 모델은 여기가 아니라 pipeline/ 아래에 둔다.
# 업로드 응답과 SSE 이벤트(API-003)는 기존 프론트 계약 그대로다.
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import CamelModel

JobStatus = Literal["queued", "running", "completed", "partial", "failed"]


class UploadResponse(CamelModel):
    job_id: UUID
    document_count: int
    # 접수 직후 상태. 진행 상태는 작업 이벤트(SSE)로 따로 받는다
    status: Literal["queued"]


# 작업 진행 상태 SSE(API-003)의 이벤트별 data 모양
class JobProgressEvent(CamelModel):
    active_step: int
    progress: int


class JobCompletedEvent(CamelModel):
    job_id: UUID
    status: Literal["completed"]
    created_card_count: int


# 실패와, 프론트 계약에 없는 부분 완료(partial)를 함께 표현한다. 문구에 저장·보류 카드 수가 들어간다
class JobFailedEvent(CamelModel):
    job_id: UUID
    message: str


# 원본 문서 열람 URL 응답. url은 expires_in초 뒤 만료되는 Storage 서명 URL
class DocumentUrlResponse(CamelModel):
    url: str
    expires_in: int


# 아래는 작업 상세 조회(GET /documents/jobs/{job_id})의 응답이다.
# 문서별 카드 생성 결과(부분 완료·검토 대기 포함)와 원문 검색 인덱싱 결과를 담는다
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
    # 원문 검색 인덱싱(document_chunks)에 청크를 저장한 문서 수와 인덱싱 경고
    indexed_documents: int = 0
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None


class InterruptedJobResult(CamelModel):
    error_code: Literal["JOB_INTERRUPTED"]


class JobResponse(CamelModel):
    job_id: UUID = Field(validation_alias="id")
    status: JobStatus
    document_ids: list[UUID]
    result: JobResult | InterruptedJobResult | None = None
    created_at: str
    updated_at: str
