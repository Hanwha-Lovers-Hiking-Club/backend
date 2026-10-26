# 문서 도메인의 URL 경로만 정의한다 (업로드 / 목록 / 상세 / 진행상황 SSE).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from fastapi import APIRouter, File, UploadFile

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
        ..., description="업로드할 문서 (pdf, docx, md / 파일당 50MB 이하)"
    ),
):
    return await service.upload_documents(files)
