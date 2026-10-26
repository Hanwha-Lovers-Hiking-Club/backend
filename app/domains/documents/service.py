# 문서 도메인의 진입점 로직.
# Storage 업로드 -> documents 행 생성 -> 파이프라인(LangGraph)을 백그라운드로 실행하고 진행 상황을 관리한다.
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from postgrest.exceptions import APIError
from storage3.exceptions import StorageException

from app.config import settings
from app.domains.documents import repository

# 확장자 -> Storage에 저장할 Content-Type.
# 브라우저가 보낸 값(md는 보통 비어 있거나 octet-stream)을 쓰면 버킷의 허용 MIME 제한에 걸려서
# 확장자로 정한다
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
}
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

STORE_FAILED_DETAIL = "문서를 저장하지 못했습니다."

# mock: 작업 상태를 서버 메모리에 둔다. 서버를 재시작하면 사라지고, 여러 대면 공유되지 않는다.
# 작업 상태 저장소(테이블 등)가 정해지면 이 dict를 읽고 쓰는 부분만 교체한다.
_jobs: dict[UUID, dict] = {}


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

    _jobs[job_id] = {
        "status": "queued",
        "document_count": len(files),
        # 파이프라인이 처리할 문서. 파이프라인 연결 시 여기서 꺼내 쓴다
        "document_ids": document_ids,
    }
    return {"job_id": job_id, "document_count": len(files), "status": "queued"}
