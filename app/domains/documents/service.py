# 문서 도메인의 진입점 로직.
# Storage 업로드 -> documents 행 생성 -> 파이프라인(LangGraph)을 백그라운드로 실행하고 진행 상황을 관리한다.
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from storage3.exceptions import StorageException

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


async def _store_files(job_id: UUID, files: list[UploadFile]) -> None:
    uploaded: list[str] = []
    try:
        for file in files:
            extension = Path(file.filename or "").suffix.lower()
            # Storage 키에는 한글/일부 특수문자를 쓸 수 없어서 원래 파일명 대신 uuid로 저장한다
            path = f"{job_id}/{uuid4()}{extension}"
            repository.upload_file(path, await file.read(), ALLOWED_EXTENSIONS[extension])
            uploaded.append(path)
    except StorageException:
        # 일부만 올라간 채로 남지 않도록 이미 올린 파일을 지운다 (삭제 실패는 응답에 영향 주지 않음)
        if uploaded:
            try:
                repository.remove_files(uploaded)
            except StorageException:
                pass
        raise HTTPException(status_code=500, detail=STORE_FAILED_DETAIL)


async def upload_documents(files: list[UploadFile]) -> dict:
    # 하나라도 통과하지 못하면 아무것도 접수하지 않도록 먼저 전부 검증한다
    for file in files:
        _validate(file)

    job_id = uuid4()
    await _store_files(job_id, files)
    _jobs[job_id] = {"status": "queued", "document_count": len(files)}
    return {"job_id": job_id, "document_count": len(files), "status": "queued"}
