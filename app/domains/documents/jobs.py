# 문서 분석 작업 상태 저장소. 작업 상태를 읽고 쓰는 곳은 전부 이 파일 함수를 거친다.
from uuid import UUID

# mock: 작업 상태를 서버 메모리에 둔다. 서버를 재시작하면 사라지고, 여러 대면 공유되지 않는다.
# 작업 상태 저장소(테이블 등)가 정해지면 이 파일만 교체한다.
_jobs: dict[UUID, dict] = {}


def create_job(job_id: UUID, job: dict) -> None:
    _jobs[job_id] = job


def get_job(job_id: UUID) -> dict | None:
    return _jobs.get(job_id)
