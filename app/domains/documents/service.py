"""Upload, background graph execution, and resumable job events."""

import asyncio
import json
import logging
import mimetypes
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic.alias_generators import to_camel

from app.domains.documents import repository
from app.domains.documents.pipeline.document import DocumentSource, ParseOptions
from app.domains.documents.pipeline.graph import get_graph
from app.domains.documents.pipeline.parsers import SUPPORTED_FORMATS
from app.domains.documents.pipeline.processing import DocumentProgress
from app.domains.documents.schemas import JobResponse, UploadedDocument, UploadResponse

logger = logging.getLogger(__name__)
MAX_FILES = 10
MAX_BYTES = ParseOptions().max_bytes
TERMINAL = frozenset({"completed", "partial", "failed"})


def _filename(raw: str | None) -> str:
    filename = (raw or "").replace("\\", "/").split("/")[-1].strip()
    if not filename or filename in {".", ".."} or len(filename) > 255:
        raise HTTPException(422, "올바른 파일명이 필요합니다.")
    if Path(filename).suffix.lower() not in SUPPORTED_FORMATS:
        raise HTTPException(422, "지원하지 않는 문서 형식입니다.")
    return filename


async def upload_documents(files: list[UploadFile], background_tasks: BackgroundTasks):
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(422, f"파일은 1개부터 {MAX_FILES}개까지 업로드할 수 있습니다.")
    filenames = [_filename(uploaded.filename) for uploaded in files]
    try:
        await repository.check_job_store()
    except Exception as exc:
        raise HTTPException(503, "문서 작업 저장소가 준비되지 않았습니다.") from exc
    staged = []
    with tempfile.TemporaryDirectory(prefix="document-upload-") as temp_dir:
        for uploaded, filename in zip(files, filenames, strict=True):
            document_id = uuid4()
            local_path = Path(temp_dir) / str(document_id)
            size = 0
            with local_path.open("wb") as target:
                while chunk := await uploaded.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, f"{filename}: 최대 50 MB를 초과했습니다.")
                    target.write(chunk)
            if size == 0:
                raise HTTPException(422, f"{filename}: 빈 파일입니다.")
            staged.append((document_id, filename, size, local_path))

        created = []
        try:
            for document_id, filename, size, local_path in staged:
                storage_path = f"uploads/{document_id}{Path(filename).suffix.lower()}"
                content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                created.append((document_id, storage_path))
                await repository.upload_file(storage_path, local_path, content_type)
                await repository.create_document(document_id, filename, size, storage_path)
            job_id = uuid4()
            await repository.create_job(job_id, [item[0] for item in staged])
        except Exception as exc:
            for document_id, storage_path in reversed(created):
                try:
                    await repository.delete_document(document_id)
                except Exception:
                    logger.exception("Failed to delete document %s after upload error", document_id)
                try:
                    await repository.remove_file(storage_path)
                except Exception:
                    logger.exception("Failed to remove uploaded file %s", storage_path)
            logger.exception("Failed to register document upload")
            raise HTTPException(503, "문서 업로드를 등록하지 못했습니다.") from exc

    background_tasks.add_task(run_job, job_id, [item[0] for item in staged])
    return UploadResponse(
        job_id=job_id,
        status="queued",
        documents=[
            UploadedDocument(document_id=item[0], original_filename=item[1], size_bytes=item[2])
            for item in staged
        ],
    )


def _camel_dump(item) -> dict:
    return {to_camel(key): value for key, value in item.model_dump(mode="json").items()}


def _result(document_ids: list[UUID], progress: dict, failures: list, draft_count: int) -> dict:
    documents = []
    for document_id in document_ids:
        item = progress.get(str(document_id))
        if item is None:
            item = DocumentProgress(document_id=document_id, status="failed")
        documents.append(_camel_dump(item))
    return {
        "documents": documents,
        "totalDocuments": len(documents),
        "draftCards": draft_count,
        "admittedCards": sum(item["admittedCards"] for item in documents),
        "reviewCards": sum(item["reviewCards"] for item in documents),
        "rejectedCards": sum(item["rejectedCards"] for item in documents),
        "skippedExperiences": sum(item["skippedExperiences"] for item in documents),
        "savedCards": sum(len(item["savedCardIds"]) for item in documents),
        "failures": [_camel_dump(item) for item in failures],
    }


async def run_job(job_id: UUID, document_ids: list[UUID]) -> None:
    progress, failures, draft_count = {}, [], 0
    try:
        await repository.record_event(job_id, "started", {"jobId": str(job_id)}, "running")
        rows = await repository.get_documents(document_ids)
        by_id = {row["id"]: row for row in rows}
        if set(by_id) != {str(item) for item in document_ids}:
            raise RuntimeError("Uploaded document metadata is missing")
        with tempfile.TemporaryDirectory(prefix="document-job-") as temp_dir:
            sources = []
            for document_id in document_ids:
                row = by_id[str(document_id)]
                suffix = Path(row["original_filename"]).suffix
                local_path = Path(temp_dir) / f"{document_id}{suffix}"
                local_path.write_bytes(await repository.download_file(row["storage_path"]))
                sources.append(
                    DocumentSource(
                        document_id=document_id,
                        original_filename=row["original_filename"],
                        path=local_path,
                    )
                )
            async for update in get_graph().astream({"sources": sources}, stream_mode="updates"):
                for stage, values in update.items():
                    if stage == "finish":
                        draft_count = len(values.get("drafts", []))
                    if stage == "complete_document":
                        progress = values.get("document_progress", progress)
                    if "processing_failures" in values:
                        failures = values["processing_failures"]
                    payload = {"stage": stage}
                    if stage == "complete_document":
                        payload["documents"] = [_camel_dump(item) for item in progress.values()]
                    await repository.record_event(job_id, "stage", payload)
        result = _result(document_ids, progress, failures, draft_count)
        statuses = [item["status"] for item in result["documents"]]
        if all(status == "completed" for status in statuses):
            status = "completed"
        elif all(status == "failed" for status in statuses):
            status = "failed"
        else:
            status = "partial"
        await repository.record_event(job_id, status, result, status)
    except Exception:
        logger.exception("Document job %s failed", job_id)
        result = _result(document_ids, progress, failures, draft_count)
        result["errorCode"] = "JOB_EXECUTION_FAILED"
        status = (
            "partial"
            if any(item["status"] == "completed" for item in result["documents"])
            else "failed"
        )
        try:
            await repository.record_event(job_id, status, result, status)
        except Exception:
            logger.exception("Could not record failure for document job %s", job_id)


async def get_job(job_id: UUID) -> JobResponse:
    try:
        row = await repository.get_job(job_id)
    except Exception as exc:
        raise HTTPException(503, "작업 상태를 조회하지 못했습니다.") from exc
    if row is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return JobResponse.model_validate(row)


async def job_events(job_id: UUID, request: Request, last_event_id: str | None):
    await get_job(job_id)
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID가 올바르지 않습니다.") from exc

    async def events():
        nonlocal cursor
        while not await request.is_disconnected():
            rows = await repository.get_events(job_id, cursor)
            for row in rows:
                cursor = row["id"]
                yield (
                    f"id: {cursor}\nevent: {row['event_type']}\n"
                    f"data: {json.dumps(row['payload'], ensure_ascii=False)}\n\n"
                )
                if row["event_type"] in TERMINAL:
                    return
            job = await repository.get_job(job_id)
            if job and job["status"] in TERMINAL and len(rows) < 100:
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
