# 문서 도메인의 URL 경로만 정의한다 (업로드 / 목록 / 상세 / 진행상황 SSE).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.sse import EventSourceResponse

from app.domains.documents import service
from app.domains.documents.schemas import UploadResponse

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="문서 업로드",
    description="문서 여러 개로 분석 작업을 접수하고, 완료를 기다리지 않고 작업 ID를 반환한다.",
)
async def upload_documents(
    files: list[UploadFile] = File(
        ..., description="업로드할 문서 (pdf, docx, md, txt, xlsx / 파일당 50MB 이하)"
    ),
):
    return await service.upload_documents(files)


@router.get(
    "/jobs/{job_id}/events",
    response_class=EventSourceResponse,
    summary="문서 분석 진행 상태 수신 (SSE)",
    description=(
        "progress(activeStep, progress) 이벤트를 상태가 바뀔 때마다 보내고, "
        "completed 또는 failed 이벤트를 보낸 뒤 스트림을 끝낸다. 없는 작업 ID는 404."
    ),
)
async def job_events(job_id: UUID, _job: dict = Depends(service.get_job)):
    async for event in service.job_events(job_id):
        yield event
