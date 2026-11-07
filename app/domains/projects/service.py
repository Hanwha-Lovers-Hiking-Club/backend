# 프로젝트 로직. 업로드 1회가 프로젝트 1개이고, 같은 이름의 프로젝트도 별개로 유지한다.
# 문서와 카드의 내용은 각 도메인을 거쳐 읽는다.
import asyncio
from uuid import UUID

from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.core.errors import internal_error
from app.db import supabase
from app.domains.documents import repository as documents_repository
from app.domains.knowledge_cards import service as knowledge_cards_service
from app.domains.knowledge_cards.service import ACTIVE_STATUS

LIST_FAILED_DETAIL = "프로젝트 목록을 조회하지 못했습니다."
GET_FAILED_DETAIL = "프로젝트를 조회하지 못했습니다."
PROJECT_NOT_FOUND_DETAIL = "프로젝트를 찾을 수 없습니다."


def create_project(name: str) -> str:
    result = supabase.table("projects").insert({"name": name}).execute()
    return result.data[0]["id"]


def rename_project(project_id: str, name: str) -> None:
    supabase.table("projects").update({"name": name}).eq("id", project_id).execute()


def delete_project(project_id: str) -> None:
    supabase.table("projects").delete().eq("id", project_id).execute()


def _to_summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "document_count": row["documents"][0]["count"],
        "card_count": row["knowledge_cards"][0]["count"],
    }


async def list_projects() -> dict:
    query = (
        supabase.table("projects")
        .select("id, name, created_at, documents(count), knowledge_cards(count)")
        .eq("knowledge_cards.status", ACTIVE_STATUS)
        .order("created_at", desc=True)
        # 이전 데이터처럼 생성 시각이 같아도 순서가 바뀌지 않도록 id로 한 번 더 정렬
        .order("id")
    )
    try:
        result = await asyncio.to_thread(query.execute)
    except APIError:
        raise internal_error(LIST_FAILED_DETAIL)
    return {"items": [_to_summary(row) for row in result.data]}


def _get_project(project_id: str) -> dict | None:
    # 없는 id일 수 있어서 .single() 대신 리스트로 받아 직접 확인한다
    result = (
        supabase.table("projects").select("id, name, created_at").eq("id", project_id).execute()
    )
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
