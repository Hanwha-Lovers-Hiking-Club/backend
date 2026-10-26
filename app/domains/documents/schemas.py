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
