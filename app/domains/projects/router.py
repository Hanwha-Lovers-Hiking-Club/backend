from uuid import UUID

from fastapi import APIRouter

from app.domains.projects import service
from app.domains.projects.schemas import ProjectDetail, ProjectListResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="프로젝트 목록 조회",
    description="프로젝트별 문서 수와 active 지식카드 수를 최신 생성순으로 반환한다.",
)
async def list_projects():
    return await service.list_projects()


@router.get(
    "/{project_id}",
    response_model=ProjectDetail,
    summary="프로젝트 상세 조회",
    description="프로젝트의 원본 문서와 active 지식카드를 반환한다. 없는 프로젝트 ID는 404.",
)
async def get_project(project_id: UUID):
    return await service.get_project(project_id)
