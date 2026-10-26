# 문서 도메인의 진입점 로직.
# Storage 업로드 -> documents 행 생성 -> 파이프라인(LangGraph)을 백그라운드로 실행하고 진행 상황을 관리한다.
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 파일당 20MB

# mock: 작업 상태를 서버 메모리에 둔다. 서버를 재시작하면 사라지고, 여러 대면 공유되지 않는다.
# 작업 상태 저장소(테이블 등)가 정해지면 이 dict를 읽고 쓰는 부분만 교체한다.
_jobs: dict[UUID, dict] = {}


def _validate(file: UploadFile) -> None:
    filename = file.filename or ""
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다: {filename}")
    if (file.size or 0) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"파일 크기가 20MB를 넘습니다: {filename}")


async def upload_documents(files: list[UploadFile]) -> dict:
    # 하나라도 통과하지 못하면 아무것도 접수하지 않도록 먼저 전부 검증한다
    for file in files:
        _validate(file)

    job_id = uuid4()
    _jobs[job_id] = {"status": "queued", "document_count": len(files)}
    return {"job_id": job_id, "document_count": len(files), "status": "queued"}
