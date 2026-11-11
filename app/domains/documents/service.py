# 문서 도메인의 진입점 로직.
# Storage 업로드 -> documents 행 생성 -> 작업(document_jobs) 등록 -> 백그라운드에서
#   1) 카드 생성 파이프라인(pipeline/graph.py)
#   2) 원문 검색 인덱싱(pipeline/search_index/)
# 을 차례로 실행하고, 진행 상황을 document_job_events에 기록한다.
#
# 외부 API(경로·업로드 응답·SSE 이벤트 이름과 payload)는 기존 프론트 계약을 유지한다.
# 내부 이벤트 이력(started/stage/completed/partial/failed)은 DB에 그대로 남기고, SSE로 보낼 때만
# 프론트용 progress/completed/failed로 변환한다(_to_frontend_event). 변환표는 STAGE_STEPS 참고.
import asyncio
import logging
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from postgrest.exceptions import APIError
from pydantic.alias_generators import to_camel
from storage3.exceptions import StorageException

from app.config import settings
from app.core.errors import internal_error
from app.core.logging import job_log_context, summarize_error
from app.core.schemas import CamelModel
from app.domains.documents import repository
from app.domains.documents.pipeline.checkpoints import release_job_checkpoints, stream_document_job
from app.domains.documents.pipeline.document import DocumentSource
from app.domains.documents.pipeline.execution_budget import JobTooLargeError
from app.domains.documents.pipeline.graph import get_graph
from app.domains.documents.pipeline.observe import finish_job_usage
from app.domains.documents.pipeline.parsers import SUPPORTED_FORMATS
from app.domains.documents.pipeline.processing import DocumentProgress
from app.domains.documents.pipeline.search_index.parse import get_converter
from app.domains.documents.pipeline.search_index.run import index_documents
from app.domains.documents.schemas import (
    DocumentUrlResponse,
    JobCompletedEvent,
    JobFailedEvent,
    JobProgressEvent,
    JobResponse,
    UploadResponse,
)

logger = logging.getLogger(__name__)

# 확장자 -> Storage에 저장할 Content-Type.
# 브라우저가 보낸 값(md는 보통 비어 있거나 octet-stream)을 쓰면 버킷의 허용 MIME 제한에 걸려서
# 확장자로 정한다
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
assert set(ALLOWED_EXTENSIONS) == SUPPORTED_FORMATS
# 카드 생성 파이프라인의 실행 예산에 맞춘 업로드 한도
MAX_FILES = 10
MAX_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
STALE_AFTER = timedelta(minutes=30)
KEEPALIVE_SECONDS = 15
TERMINAL = frozenset({"completed", "partial", "failed"})

JOB_NOT_FOUND_DETAIL = "작업을 찾을 수 없습니다."
DOCUMENT_NOT_FOUND_DETAIL = "문서를 찾을 수 없습니다."
URL_FAILED_DETAIL = "원본 문서 주소를 만들지 못했습니다."

# 원본 문서 서명 URL 유효 시간(초). 프론트는 클릭할 때마다 새로 받는다
SIGNED_URL_EXPIRES_IN = 600
# 브라우저가 바로 열 수 있는 형식. 나머지(docx/xlsx/md)는 원래 파일명으로 내려받게 한다
# (Storage 키가 uuid라 그냥 두면 uuid 파일명으로 받아진다)
INLINE_EXTENSIONS = {".pdf", ".txt"}

# 프론트 진행 단계(activeStep 0~8)별 progress 값. 단계 문구는 프론트 processingSteps가 가지고,
# 백엔드는 번호만 보낸다. 단계 순서: 0 parse, 1 chunk, 2 extract, 3 draft_card, 4 verify,
# 5 embed, 6 save, 7 원문 청크 임베딩, 8 원문 청크 저장
STEP_PROGRESS = [11, 22, 33, 44, 56, 67, 78, 89, 100]
# 내부 stage 이벤트(그 단계가 끝났다는 기록) -> 그다음 실행 중인 프론트 단계 번호.
# activeStep=k는 "k번째 단계가 지금 실행 중"이라는 뜻이다. 재처리 루프나 문서별 반복으로 앞 단계가
# 다시 돌아도 진행 표시는 뒤로 가지 않는다(가장 큰 값 유지). 표에 없는 stage는 단계를 바꾸지 않는다
STAGE_STEPS = {
    "initialize": 0,
    "parse": 0,
    "normalize": 1,
    "chunk": 2,
    "prepare_extraction": 2,
    "extract": 2,
    "merge_extraction": 2,
    "select_candidates": 3,
    "draft_card": 4,
    "verify": 4,
    "settle": 4,
    "prepare_retry": 4,
    "finish": 5,
    "build_embedding_texts": 5,
    "embed": 6,
    "save": 6,
    "complete_document": 6,
    # 원문 검색 인덱싱(search_index). 검색용 parse/chunk도 7단계에 포함한다
    "search_index": 7,
    "index_parse": 7,
    "index_chunk": 7,
    "embed_chunks": 8,
    "save_chunks": 8,
}


def _filename(raw: str | None) -> str:
    filename = (raw or "").replace("\\", "/").split("/")[-1].strip()
    if not filename or filename in {".", ".."} or len(filename) > 255:
        raise HTTPException(422, "올바른 파일명이 필요합니다.")
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"지원하지 않는 파일 형식입니다: {filename}")
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
    total_size = 0
    with tempfile.TemporaryDirectory(prefix="document-upload-") as temp_dir:
        for uploaded, filename in zip(files, filenames, strict=True):
            document_id = uuid4()
            local_path = Path(temp_dir) / str(document_id)
            size = 0
            with local_path.open("wb") as target:
                while chunk := await uploaded.read(1024 * 1024):
                    size += len(chunk)
                    total_size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, f"{filename}: 파일당 5 MB를 초과했습니다.")
                    if total_size > MAX_TOTAL_BYTES:
                        raise HTTPException(413, "업로드 합계 10 MB를 초과했습니다.")
                    target.write(chunk)
            if size == 0:
                raise HTTPException(422, f"{filename}: 빈 파일입니다.")
            staged.append((document_id, filename, size, local_path))

        created = []
        try:
            for document_id, filename, size, local_path in staged:
                extension = Path(filename).suffix.lower()
                storage_path = f"uploads/{document_id}{extension}"
                created.append((document_id, storage_path))
                await repository.upload_file(
                    storage_path, local_path, ALLOWED_EXTENSIONS[extension]
                )
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
    return UploadResponse(job_id=job_id, document_count=len(staged), status="queued")


def _warm_up_converter() -> None:
    started = time.perf_counter()
    try:
        get_converter()
    except Exception:
        logger.exception("문서 변환기 미리 로딩 실패. 첫 업로드 때 다시 시도한다")
        return
    logger.info("문서 변환기 준비 완료 (%.1f초)", time.perf_counter() - started)


def start_converter_warm_up() -> threading.Thread:
    # 서버가 켜질 때 한 번 부른다. 원문 검색 인덱싱(search_index/parse.py)이 쓰는 문서
    # 변환기(docling)를 불러오는 데 ~40초(개발 PC 기준) 걸려서 첫 업로드가 느려지지 않게 미리
    # 불러 둔다. 서버 시작을 막지 않도록 따로 돌고, 서버를 끌 때 붙잡지 않도록 daemon으로 돌린다.
    # 로딩 중에 업로드가 들어와도 get_converter의 잠금 덕에 한 번만 로딩한다
    thread = threading.Thread(target=_warm_up_converter, name="converter-warm-up", daemon=True)
    thread.start()
    return thread


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


async def _record_stage(job_id: UUID, stage: str, payload: dict | None = None) -> None:
    await repository.record_event(job_id, "stage", {"stage": stage, **(payload or {})})


class _CardPipelineError(RuntimeError):
    # 카드 그래프가 중간에 실패했을 때 그때까지의 문서별 기록을 함께 올린다
    def __init__(self, progress: dict, failures: list, draft_count: int):
        super().__init__("card pipeline failed")
        self.progress, self.failures, self.draft_count = progress, failures, draft_count


async def _run_card_pipeline(job_id: UUID, rows: list[dict], document_ids: list[UUID]):
    # 카드 생성 그래프를 실행하고 (문서별 진행, 실패 목록, 초안 수)를 돌려준다
    progress, failures, draft_count = {}, [], 0
    by_id = {row["id"]: row for row in rows}
    try:
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
            async for update in stream_document_job(job_id, sources, graph_factory=get_graph):
                for stage, values in update.items():
                    if stage == "finish":
                        draft_count = len(values.get("drafts", []))
                    if stage == "complete_document":
                        progress = values.get("document_progress", progress)
                    if "processing_failures" in values:
                        failures = values["processing_failures"]
                    payload = {}
                    if stage == "complete_document":
                        payload["documents"] = [_camel_dump(item) for item in progress.values()]
                    await _record_stage(job_id, stage, payload)
    except Exception as exc:
        raise _CardPipelineError(progress, failures, draft_count) from exc
    return progress, failures, draft_count


async def _run_search_index(job_id: UUID, rows: list[dict], document_ids: list[UUID]) -> dict:
    # 원문 검색 인덱싱. 카드 경로의 성공 여부와 무관하게 실행하고, 실패해도 작업을 실패시키지 않는다
    by_id = {row["id"]: row for row in rows}
    documents = [
        {
            "id": document_id,
            "storage_bucket": settings.SUPABASE_STORAGE_BUCKET,
            "storage_path": by_id[str(document_id)]["storage_path"],
            "original_filename": by_id[str(document_id)]["original_filename"],
        }
        for document_id in document_ids
        if str(document_id) in by_id
    ]

    async def on_step(stage: str) -> None:
        try:
            await _record_stage(job_id, stage)
        except Exception:
            logger.exception("원문 인덱싱 진행 기록 실패 (%s)", stage)

    await on_step("search_index")
    try:
        state = await index_documents(job_id, documents, on_step)
    except Exception as exc:
        logger.exception("원문 검색 인덱싱 중 예기치 못한 오류")
        return {
            "indexedDocuments": 0,
            "warnings": [f"원문 검색 인덱싱 실패: {summarize_error(exc)}"[:500]],
        }
    warnings = list(state.get("warnings", []))
    if state.get("error"):
        warnings.append(f"원문 검색 인덱싱 중단: {state['error']}"[:500])
    return {"indexedDocuments": state.get("indexed_document_count", 0), "warnings": warnings}


async def run_job(job_id: UUID, document_ids: list[UUID]) -> None:
    # 어떤 경우에도 작업이 종료 상태(completed/partial/failed)로 기록되어야 SSE가 멈추지 않는다.
    # 카드 경로가 실패해도 원문 검색 인덱싱은 실행한다(dev의 "카드 실패 -> 인덱싱 진행" 정책).
    # 종료 상태는 카드 경로의 문서별 결과로 정하고, 인덱싱 결과는 result에 함께 남긴다
    with job_log_context(job_id):
        progress, failures, draft_count = {}, [], 0
        error_code = None
        rows: list[dict] = []
        try:
            await repository.record_event(job_id, "started", {"jobId": str(job_id)}, "running")
            rows = await repository.get_documents(document_ids)
            if {row["id"] for row in rows} != {str(item) for item in document_ids}:
                raise RuntimeError("Uploaded document metadata is missing")
            progress, failures, draft_count = await _run_card_pipeline(job_id, rows, document_ids)
        except Exception as exc:
            logger.exception("Document job %s failed in the card pipeline", job_id)
            cause = exc
            if isinstance(exc, _CardPipelineError):
                cause = exc.__cause__
                progress, failures, draft_count = exc.progress, exc.failures, exc.draft_count
            error_code = (
                cause.code if isinstance(cause, JobTooLargeError) else "JOB_EXECUTION_FAILED"
            )

        if rows:
            index = await _run_search_index(job_id, rows, document_ids)
        else:
            index = {
                "indexedDocuments": 0,
                "warnings": ["문서 정보를 읽지 못해 원문 검색 인덱싱을 건너뜀"],
            }

        result = _result(document_ids, progress, failures, draft_count)
        result.update(index)
        statuses = [item["status"] for item in result["documents"]]
        if error_code:
            result["errorCode"] = error_code
            status = "partial" if any(item == "completed" for item in statuses) else "failed"
        elif all(item == "completed" for item in statuses):
            status = "completed"
        elif all(item == "failed" for item in statuses):
            status = "failed"
        else:
            status = "partial"
        try:
            await repository.record_event(job_id, status, result, status)
        except Exception:
            logger.exception("Could not record final status for document job %s", job_id)
            status = None
        if status == "completed":
            try:
                await release_job_checkpoints(job_id)
            except Exception:
                # 정리 실패로 이미 영속 기록된 성공 결과를 실패로 덮어쓰지 않는다.
                logger.exception("Could not clean completed job checkpoints %s", job_id)
        finish_job_usage(str(job_id))
        logger.info(
            "작업 종료(%s): 카드 %d개 저장, 원문 검색 가능 문서 %d/%d개 (경고 %d건)",
            status,
            result["savedCards"],
            result["indexedDocuments"],
            len(document_ids),
            len(result["warnings"]),
        )


async def _load_job(job_id: UUID) -> dict | None:
    row = await repository.get_job(job_id)
    if row and row["status"] in {"queued", "running"}:
        updated_at = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
        if updated_at <= datetime.now(timezone.utc) - STALE_AFTER:
            await repository.expire_stale_job(job_id)
            row = await repository.get_job(job_id)
    return row


async def get_job(job_id: UUID) -> JobResponse:
    try:
        row = await _load_job(job_id)
    except Exception as exc:
        raise HTTPException(503, "작업 상태를 조회하지 못했습니다.") from exc
    if row is None:
        raise HTTPException(404, JOB_NOT_FOUND_DETAIL)
    return JobResponse.model_validate(row)


def _failure_message(event_type: str, payload: dict) -> str:
    # 프론트 계약에는 partial이 없다. 일부 결과만 저장된 작업을 성공(completed)으로 보내지 않고
    # failed로 보내되, 저장된 카드·보류 카드 수를 문구에 남긴다. 상세는 작업 조회 API가 돌려준다
    code = payload.get("errorCode")
    if code == "JOB_INTERRUPTED":
        return "문서 분석이 중단되었습니다. 30분 이상 진행 기록이 없습니다."
    if event_type == "partial":
        documents = payload.get("documents", [])
        unfinished = sum(item.get("status") != "completed" for item in documents)
        return (
            "일부 문서의 카드 생성이 완료되지 않았습니다. "
            f"완료되지 않은 문서 {unfinished}/{len(documents)}개, "
            f"저장된 카드 {payload.get('savedCards', 0)}개, "
            f"검토 대기 카드 {payload.get('reviewCards', 0)}개."
        )
    suffix = f" ({code})" if code else ""
    return f"문서 분석에 실패했습니다{suffix}."


def _to_frontend_event(job_id: UUID, row: dict, step: int) -> tuple[str, CamelModel, int]:
    # 내부 이벤트 1건 -> (프론트 이벤트 이름, payload, 갱신된 단계)
    event_type, payload = row["event_type"], row["payload"] or {}
    if event_type == "completed":
        event = JobCompletedEvent(
            job_id=job_id, status="completed", created_card_count=payload.get("savedCards", 0)
        )
        return "completed", event, step
    if event_type in {"partial", "failed"}:
        message = _failure_message(event_type, payload)
        return "failed", JobFailedEvent(job_id=job_id, message=message), step
    if event_type == "stage":
        step = max(step, STAGE_STEPS.get(payload.get("stage"), step))
    return "progress", JobProgressEvent(active_step=step, progress=STEP_PROGRESS[step]), step


def _sse(event_id: int, event: str, payload: CamelModel) -> str:
    data = payload.model_dump_json(by_alias=True)
    return f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"


async def job_events(job_id: UUID, request: Request, last_event_id: str | None):
    await get_job(job_id)
    try:
        resume_after = max(0, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID가 올바르지 않습니다.") from exc

    async def events():
        # dev 계약: 접속하자마자 현재 상태 하나를 보내고(이미 끝난 작업이면 종료 이벤트만),
        # 이후엔 바뀔 때만 보낸다. 현재 상태는 이력을 처음부터 읽어 계산한다.
        # Last-Event-ID로 재접속하면 그 시점의 단계와 같을 때는 progress를 다시 보내지 않는다.
        # 종료 이벤트는 이미 받았더라도 다시 보낸다(받지 못하면 EventSource가 재접속을 반복한다)
        cursor, step = 0, 0
        resume_step: int | None = None
        while True:
            rows = await repository.get_events(job_id, cursor)
            for row in rows:
                cursor = row["id"]
                name, payload, step = _to_frontend_event(job_id, row, step)
                if name != "progress":
                    yield _sse(cursor, name, payload)
                    return
                if cursor <= resume_after:
                    resume_step = step
            if len(rows) < 100:
                break
        sent_step = resume_step if resume_after else None
        if step != sent_step:
            yield _sse(
                cursor, "progress", JobProgressEvent(active_step=step, progress=STEP_PROGRESS[step])
            )
            sent_step = step
        last_emit = time.monotonic()
        while not await request.is_disconnected():
            rows = await repository.get_events(job_id, cursor)
            for row in rows:
                cursor = row["id"]
                name, payload, step = _to_frontend_event(job_id, row, step)
                if name != "progress":
                    yield _sse(cursor, name, payload)
                    return
                if step != sent_step:
                    yield _sse(cursor, name, payload)
                    sent_step = step
                    last_emit = time.monotonic()
            if len(rows) >= 100:
                continue
            job = await _load_job(job_id)
            if job and job["status"] in TERMINAL:
                if not await repository.get_events(job_id, cursor):
                    return
                continue
            if time.monotonic() - last_emit >= KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_emit = time.monotonic()
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def get_document_url(document_id: UUID) -> DocumentUrlResponse:
    try:
        document = repository.get_document(str(document_id))
    except APIError:
        raise internal_error(URL_FAILED_DETAIL)
    if document is None:
        raise HTTPException(status_code=404, detail=DOCUMENT_NOT_FOUND_DETAIL)

    filename = document["original_filename"]
    inline = Path(filename).suffix.lower() in INLINE_EXTENSIONS
    try:
        url = repository.create_signed_url(
            document["storage_path"],
            SIGNED_URL_EXPIRES_IN,
            download=None if inline else filename,
        )
    except StorageException:
        raise internal_error(URL_FAILED_DETAIL)
    return DocumentUrlResponse(url=url, expires_in=SIGNED_URL_EXPIRES_IN)
