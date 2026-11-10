from uuid import UUID

from fastapi import APIRouter, Query

from app.domains.projects import service
from app.domains.projects.schemas import ProjectDetail, ProjectListResponse, ProjectSort

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="프로젝트 목록 조회",
    description=(
        "프로젝트 이름·설명·팀·대표 기술·유형 검색, 정렬, 페이지네이션 결과와 "
        "전체 개수를 반환한다. 카드 수는 active 기준이다."
    ),
)
async def list_projects(
    q: str = Query("", description="이름/설명/팀/대표 기술 부분일치 + 유형(프론트, AI 등) 검색"),
    sort: ProjectSort = Query("created", description="created: 최신 생성순 / name: 이름순"),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, alias="pageSize", ge=1, le=100),
):
    return await service.list_projects(q, sort, page, page_size)


@router.get(
    "/{project_id}",
    response_model=ProjectDetail,
    summary="프로젝트 상세 조회",
    description="프로젝트의 원본 문서와 active 지식카드를 반환한다. 없는 프로젝트 ID는 404.",
)
async def get_project(project_id: UUID):
    return await service.get_project(project_id)
