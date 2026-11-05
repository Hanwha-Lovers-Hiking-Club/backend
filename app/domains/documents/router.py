# 문서 도메인의 URL 경로만 정의한다 (업로드 / 작업 조회 / 진행상황 SSE / 원본 URL).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Header, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.domains.documents import service
from app.domains.documents.schemas import DocumentUrlResponse, JobResponse, UploadResponse

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="문서 업로드",
    description=(
        "문서 여러 개로 분석 작업을 접수하고, 완료를 기다리지 않고 작업 ID를 반환한다. "
        "파일 1~10개, 파일당 5MB·합계 10MB 이하. 빈 파일은 거부한다."
    ),
)
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="업로드할 문서 (pdf, docx, md, txt, xlsx)"),
):
    return await service.upload_documents(files, background_tasks)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="문서 분석 작업 조회",
    description="작업 상태와 문서별 카드 생성·원문 인덱싱 결과를 반환한다. 없는 작업 ID는 404.",
)
async def get_job(job_id: UUID):
    return await service.get_job(job_id)


@router.get(
    "/jobs/{job_id}/events",
    response_model=None,
    summary="문서 분석 진행 상태 수신 (SSE)",
    description=(
        "progress(activeStep, progress) 이벤트를 상태가 바뀔 때마다 보내고, "
        "completed 또는 failed 이벤트를 보낸 뒤 스트림을 끝낸다. 일부 문서만 완료된 작업은 "
        "failed로 보내며 message에 저장·보류 카드 수를 담는다. Last-Event-ID로 이어 받을 수 있다. "
        "없는 작업 ID는 404."
    ),
)
async def job_events(
    job_id: UUID, request: Request, last_event_id: str | None = Header(None)
) -> StreamingResponse:
    return await service.job_events(job_id, request, last_event_id)


@router.get(
    "/{document_id}/url",
    response_model=DocumentUrlResponse,
    summary="원본 문서 열람 URL 발급",
    description=(
        "원본 파일의 서명 URL(expiresIn초 뒤 만료)을 반환한다. "
        "pdf/txt는 브라우저에서 열리고, 나머지 형식은 원래 파일명으로 내려받아진다. "
        "없는 문서 ID는 404."
    ),
)
def get_document_url(document_id: UUID):
    # service가 동기 Supabase 호출을 하므로, FastAPI가 스레드에서 실행하도록 def로 둔다
    return service.get_document_url(document_id)
