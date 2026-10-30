# 문서 API의 요청/응답 모양(pydantic 모델)만 정의한다.
# 파이프라인 내부에서 쓰는 모델은 여기가 아니라 pipeline/models.py에 둔다.
from typing import Literal
from uuid import UUID

from app.core.schemas import CamelModel


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


class JobFailedEvent(CamelModel):
    job_id: UUID
    message: str


# 원본 문서 열람 URL 응답. url은 expires_in초 뒤 만료되는 Storage 서명 URL
class DocumentUrlResponse(CamelModel):
    url: str
    expires_in: int
