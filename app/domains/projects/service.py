# 프로젝트 로직. 업로드 1회가 프로젝트 1개이고, 같은 이름의 프로젝트도 별개로 유지한다.
# 문서와 카드의 내용은 각 도메인을 거쳐 읽는다.
import asyncio
from uuid import UUID

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.core.errors import internal_error
from app.core.text import strip_nul
from app.db import supabase
from app.domains.documents import repository as documents_repository
from app.domains.knowledge_cards import service as knowledge_cards_service
from app.domains.knowledge_cards.service import ACTIVE_STATUS
from app.domains.projects.schemas import ProjectSort

LIST_FAILED_DETAIL = "프로젝트 목록을 조회하지 못했습니다."
GET_FAILED_DETAIL = "프로젝트를 조회하지 못했습니다."
PROJECT_NOT_FOUND_DETAIL = "프로젝트를 찾을 수 없습니다."

PROJECT_COLUMNS = "id, name, domain, team, period, description, stack, created_at"


def create_project(name: str) -> str:
    result = supabase.table("projects").insert({"name": name}).execute()
    return result.data[0]["id"]


def update_project(project_id: str, fields: dict) -> None:
    supabase.table("projects").update(strip_nul(fields)).eq("id", project_id).execute()


def delete_project(project_id: str) -> None:
    supabase.table("projects").delete().eq("id", project_id).execute()


def _to_summary(row: dict) -> dict:
    return {
        **row,
        "document_count": row["documents"][0]["count"],
        "card_count": row["knowledge_cards"][0]["count"],
    }


async def list_projects(q: str, sort: ProjectSort, page: int, page_size: int) -> dict:
    q = q.strip()
    start = (page - 1) * page_size
    query = (
        supabase.table("projects")
        .select(f"{PROJECT_COLUMNS}, documents(count), knowledge_cards(count)", count="exact")
        .ilike("name", f"%{q}%")
        .eq("knowledge_cards.status", ACTIVE_STATUS)
        .order("name" if sort == "name" else "created_at", desc=sort != "name")
        # 이전 데이터처럼 생성 시각이 같아도 순서가 바뀌지 않도록 id로 한 번 더 정렬
        .order("id")
        .range(start, start + page_size - 1)
    )
    try:
        result = await asyncio.to_thread(query.execute)
    except APIError as e:
        if e.code != knowledge_cards_service.RANGE_NOT_SATISFIABLE:
            raise internal_error(LIST_FAILED_DETAIL)
        # 범위를 벗어나도 검색 결과의 전체 개수는 유지한다 (기존 카드 목록과 같은 동작).
        count_query = (
            supabase.table("projects")
            .select("id", count="exact", head=True)
            .ilike("name", f"%{q}%")
        )
        try:
            result = await asyncio.to_thread(count_query.execute)
        except APIError:
            raise internal_error(LIST_FAILED_DETAIL)
        return {"items": [], "total": result.count or 0}
    return {"items": [_to_summary(row) for row in result.data], "total": result.count or 0}


def _get_project(project_id: str) -> dict | None:
    # 없는 id일 수 있어서 .single() 대신 리스트로 받아 직접 확인한다
    result = supabase.table("projects").select(PROJECT_COLUMNS).eq("id", project_id).execute()
    if not result.data:
        return None
    return {
        **result.data[0],
        "documents": documents_repository.list_project_documents(project_id),
        "cards": knowledge_cards_service.list_project_cards(project_id),
    }


async def get_project(project_id: UUID) -> dict:
    try:
        project = await asyncio.to_thread(_get_project, str(project_id))
    except APIError:
        raise internal_error(GET_FAILED_DETAIL)
    if project is None:
        raise HTTPException(status_code=404, detail=PROJECT_NOT_FOUND_DETAIL)
    return project
