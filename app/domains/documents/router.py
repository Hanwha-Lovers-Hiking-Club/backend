from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Header, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.domains.documents import service
from app.domains.documents.schemas import JobResponse, UploadResponse

router = APIRouter(tags=["documents"])


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    return await service.upload_documents(files, background_tasks)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID):
    return await service.get_job(job_id)


@router.get("/jobs/{job_id}/events", response_model=None)
async def job_events(
    job_id: UUID, request: Request, last_event_id: str | None = Header(None)
) -> StreamingResponse:
    return await service.job_events(job_id, request, last_event_id)
