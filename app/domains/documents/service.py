# 문서 도메인의 진입점 로직.
# Storage 업로드 -> documents 행 생성 -> 파이프라인(LangGraph)을 백그라운드로 실행하고
# 진행 상황을 관리한다.
import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from fastapi.sse import ServerSentEvent
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException

from app.config import settings
from app.core.logging import job_log_context, summarize_error
from app.core.sse import to_sse
from app.domains.documents import jobs, repository
from app.domains.documents.pipeline.graph import NODE_ORDER, build_graph
from app.domains.documents.pipeline.nodes.parse import get_converter
from app.domains.documents.pipeline.observe import finish_job_usage
from app.domains.documents.schemas import JobCompletedEvent, JobFailedEvent, JobProgressEvent

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
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

STORE_FAILED_DETAIL = "문서를 저장하지 못했습니다."
JOB_NOT_FOUND_DETAIL = "작업을 찾을 수 없습니다."

# 진행 단계(activeStep 0~8)별 progress 값. 단계 하나가 그래프 노드 하나(pipeline.graph.NODE_ORDER와
# 같은 순서, 같은 개수)다. activeStep=k는 "k번째 노드가 지금 실행 중"이라는 뜻.
# 단계 문구는 프론트 processingSteps가 가지고, 백엔드는 번호만 보낸다.
# ponytail: 값은 균등 분할이다. LLM 노드가 들어오면 훨씬 오래 걸리므로
# 실제 문서 테스트의 노드별 소요 시간 로그를 보고 조정한다
STEP_PROGRESS = [11, 22, 33, 44, 56, 67, 78, 89, 100]
# 진행 상태 SSE가 작업 상태를 확인하는 간격(초)
POLL_INTERVAL = 0.5


def _validate(file: UploadFile) -> None:
    filename = file.filename or ""
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다: {filename}")
    if (file.size or 0) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413, detail=f"파일 크기가 {MAX_FILE_SIZE_MB}MB를 넘습니다: {filename}"
        )


def _remove_quietly(paths: list[str]) -> None:
    # 일부만 올라간 채로 남지 않도록 이미 올린 파일을 지운다 (삭제 실패는 응답에 영향 주지 않음)
    if not paths:
        return
    try:
        repository.remove_files(paths)
    except StorageException:
        pass


async def _store_files(job_id: UUID, files: list[UploadFile]) -> list[dict]:
    # Storage에 올리고, documents 테이블에 넣을 행을 만들어 돌려준다
    rows: list[dict] = []
    try:
        for file in files:
            filename = file.filename or ""
            extension = Path(filename).suffix.lower()
            # Storage 키에는 한글/일부 특수문자를 쓸 수 없어서 원래 파일명 대신 uuid로 저장한다
            path = f"{job_id}/{uuid4()}{extension}"
            content = await file.read()
            repository.upload_file(path, content, ALLOWED_EXTENSIONS[extension])
            rows.append(
                {
                    # 제목은 파이프라인에서 LLM이 뽑기 전까지 파일명(확장자 제외)으로 둔다
                    "title": Path(filename).stem.strip() or filename,
                    "original_filename": filename,
                    "size_bytes": len(content),
                    "storage_bucket": settings.SUPABASE_STORAGE_BUCKET,
                    "storage_path": path,
                }
            )
    except StorageException:
        _remove_quietly([row["storage_path"] for row in rows])
        raise HTTPException(status_code=500, detail=STORE_FAILED_DETAIL)
    return rows


async def upload_documents(files: list[UploadFile]) -> dict:
    # 하나라도 통과하지 못하면 아무것도 접수하지 않도록 먼저 전부 검증한다
    for file in files:
        _validate(file)

    job_id = uuid4()
    rows = await _store_files(job_id, files)
    try:
        # 한 번의 insert로 넣어서 문서 행도 전부 들어가거나 하나도 안 들어간다
        document_ids = repository.insert_documents(rows)
    except APIError:
        _remove_quietly([row["storage_path"] for row in rows])
        raise HTTPException(status_code=500, detail=STORE_FAILED_DETAIL)

    # 파이프라인(parse 노드)이 Storage에서 파일을 내려받는 데 필요한 정보
    documents = [
        {
            "id": document_id,
            "storage_bucket": row["storage_bucket"],
            "storage_path": row["storage_path"],
            "original_filename": row["original_filename"],
        }
        for document_id, row in zip(document_ids, rows, strict=True)
    ]
    jobs.create_job(
        job_id,
        {
            "status": "queued",
            "document_count": len(files),
            "documents": documents,
            "active_step": 0,
            "progress": STEP_PROGRESS[0],
        },
    )
    _start_pipeline(job_id, documents)
    return {"job_id": job_id, "document_count": len(files), "status": "queued"}


def _warm_up_converter() -> None:
    started = time.perf_counter()
    try:
        get_converter()
    except Exception:
        logger.exception("문서 변환기 미리 로딩 실패. 첫 업로드 때 다시 시도한다")
        return
    logger.info("문서 변환기 준비 완료 (%.1f초)", time.perf_counter() - started)


def start_converter_warm_up() -> threading.Thread:
    # 서버가 켜질 때 한 번 부른다. 문서 변환기(docling)를 불러오는 데 ~40초(개발 PC 기준)
    # 걸려서 첫 업로드가 느려지지 않게 미리 불러 둔다. 서버 시작을 막지 않도록 따로 돌고,
    # 서버를 끌 때 붙잡지 않도록 daemon으로 돌린다.
    # 로딩 중에 업로드가 들어와도 get_converter의 잠금 덕에 한 번만 로딩한다
    thread = threading.Thread(target=_warm_up_converter, name="converter-warm-up", daemon=True)
    thread.start()
    return thread


# 백그라운드 task는 참조가 없으면 실행 도중 GC로 사라질 수 있어서 끝날 때까지 여기서 붙잡아 둔다
_background_tasks: set[asyncio.Task] = set()


def _start_pipeline(job_id: UUID, documents: list[dict]) -> None:
    # 업로드 응답은 기다리지 않고 바로 돌려주고, 분석은 같은 서버 프로세스에서 따로 돌린다.
    # ponytail: 서버를 재시작하면 진행 중인 job은 사라진다(상태도 메모리라 같이 사라짐).
    # 재시작에도 살아남아야 하면 작업 큐(별도 워커)와 job 테이블이 필요하다
    task = asyncio.create_task(_run_pipeline(job_id, documents))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _fail(job_id: UUID, message: str) -> None:
    logger.error("작업 실패: %s", message)
    jobs.update_job(job_id, status="failed", message=message)


async def _run_graph(job_id: UUID, documents: list[dict]) -> dict:
    # 노드가 하나 끝날 때마다 알림을 받아 진행 단계를 올리고, 그 결과를 모아 최종 state를 만든다
    final: dict = {"job_id": job_id, "documents": documents}
    async for update in build_graph().astream(dict(final), stream_mode="updates"):
        for node, delta in update.items():
            final.update(delta or {})
            next_step = NODE_ORDER.index(node) + 1
            if next_step < len(NODE_ORDER):
                jobs.update_job(job_id, active_step=next_step, progress=STEP_PROGRESS[next_step])
    return final


async def _run_pipeline(job_id: UUID, documents: list[dict]) -> None:
    # 어떤 경우에도 job이 completed나 failed로 끝나야 SSE가 멈추지 않는다.
    # 노드 안의 오류는 그래프가 state["error"]로 바꿔 주고, 그래프 밖에서 나는 예외는 여기서 잡는다
    with job_log_context(job_id):
        jobs.update_job(job_id, status="running")
        logger.info("작업 시작: 문서 %d개", len(documents))
        try:
            final = await _run_graph(job_id, documents)
        except Exception as e:
            logger.exception("작업 실행 중 예기치 못한 오류")
            finish_job_usage(str(job_id))
            _fail(job_id, f"문서 분석 중 오류가 발생했습니다: {summarize_error(e)}")
            return

        if final.get("error"):
            _fail(job_id, final["error"])
            return
        # 카드가 0개여도(경험 없는 문서, 카드 생성 실패, 읽지 못한 문서가 있어 카드를 건너뜀)
        # 원문 검색용 청크가 저장됐으면 실패가 아니다. 쓸 수 있는 결과(카드 또는 검색 가능한 문서)가
        # 하나도 없을 때만 실패다. 그 구분과 사유(warnings)는 해커톤 범위에서는 프론트로 보내지 않고
        # job과 로그에만 남긴다
        card_count = final.get("saved_card_count", 0)
        indexed = final.get("indexed_document_count", 0)
        warnings = list(final.get("warnings", []))
        if final.get("card_error"):
            warnings.append(f"카드 생성 실패: {final['card_error']}")
        if final.get("parse_failures"):
            warnings.append("읽지 못한 문서가 있어 카드 생성을 건너뜀")
        if card_count == 0 and indexed == 0:
            reasons = " / ".join(warnings) or "저장된 결과 없음"
            _fail(job_id, f"저장된 카드와 검색 가능한 문서가 없습니다: {reasons}"[:500])
            return
        logger.info(
            "작업 완료: 카드 %d개 저장, 검색 가능 문서 %d/%d개 (경고 %d건)",
            card_count,
            indexed,
            len(documents),
            len(warnings),
        )
        jobs.update_job(
            job_id, status="completed", created_card_count=card_count, warnings=warnings
        )


def get_job(job_id: UUID) -> dict:
    # router의 Depends로 써서 스트림이 시작되기 전에 404를 응답한다
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND_DETAIL)
    return job


async def _wait() -> None:
    await asyncio.sleep(POLL_INTERVAL)


async def job_events(job_id: UUID) -> AsyncIterator[ServerSentEvent]:
    # 접속하자마자 현재 상태를 보내고(재연결해도 최신 상태부터), 이후엔 바뀔 때만 보낸다.
    # 지금은 메모리 dict를 주기적으로 확인한다. 저장소가 바뀌면 상태를 읽는 부분만 교체한다.
    last_sent = None
    while (job := jobs.get_job(job_id)) is not None:
        if job["status"] == "completed":
            yield to_sse(
                "completed",
                JobCompletedEvent(
                    job_id=job_id,
                    status="completed",
                    created_card_count=job["created_card_count"],
                ),
            )
            return
        if job["status"] == "failed":
            yield to_sse("failed", JobFailedEvent(job_id=job_id, message=job["message"]))
            return

        current = (job["active_step"], job["progress"])
        if current != last_sent:
            yield to_sse("progress", JobProgressEvent(active_step=current[0], progress=current[1]))
            last_sent = current
        await _wait()
