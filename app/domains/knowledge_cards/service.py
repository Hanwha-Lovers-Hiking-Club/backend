# 지식카드 조회와 검증된 문서의 원자적 저장을 담당한다.
import asyncio

import httpx
from fastapi import HTTPException
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.db import supabase
from app.domains.knowledge_cards.contracts import (
    VECTOR_COLUMNS,
    CardPersistenceError,
    DocumentCardsReceipt,
    DocumentCardsWrite,
)
from app.domains.knowledge_cards.schemas import CardSort

# 근거(card_documents)와 근거 문서(documents)를 한 번에 가져온다
CARD_SELECT = (
    "id,title,summary,context,problem,cause,approach,rationale,outcome,lesson,"
    "technologies,keywords,status,quality_score,last_verified_at,verification_version,"
    "project_name,department,project_date,created_at,updated_at,"
    "card_documents(id,document_id,location,excerpt,documents(title,original_filename))"
)

# (정렬 컬럼, 내림차순 여부)
SORT_COLUMNS: dict[str, tuple[str, bool]] = {
    "recent": ("updated_at", True),
    "created": ("created_at", True),
    "name": ("title", False),
}

# PostgREST: 요청한 범위가 전체 결과 수를 넘어선 경우의 에러 코드
RANGE_NOT_SATISFIABLE = "PGRST103"

LIST_FAILED_DETAIL = "지식카드 목록을 조회하지 못했습니다."


async def save_document_cards(request: dict) -> dict:
    """일반 dict로 문서별 pass 카드·근거·벡터를 받아 RPC 한 번으로 저장한다.

    같은 문서 ID와 해시는 기존 결과를 반환하며 다른 카드 집합은 아직 지원하지 않는다.
    main의 단건 create_card는 두 DB 호출이라 이 트랜잭션을 대신해 반복 호출하지 않는다.
    """
    from app.config import settings

    try:
        payload = DocumentCardsWrite.model_validate(request)
        for card in payload.cards:
            for column in VECTOR_COLUMNS:
                vector = getattr(card, column)
                if vector is not None and (
                    len(vector) != settings.EMBEDDING_DIMENSIONS
                    or not any(value != 0 for value in vector)
                ):
                    raise ValueError("Embedding does not match configured dimensions")
    except (ValidationError, ValueError) as exc:
        raise CardPersistenceError("Invalid document save request") from exc

    rows = []
    for card in payload.cards:
        row = card.model_dump(mode="json")
        row["evidences"] = [ref.model_dump(exclude={"document_id"}) for ref in card.evidences]
        rows.append(row)
    params = {
        "p_document_id": str(payload.document_id),
        "p_payload_hash": payload.payload_hash,
        "p_cards": rows,
    }
    try:
        result = await asyncio.to_thread(
            lambda: supabase.rpc("save_document_cards", params).execute()
        )
    except (APIError, httpx.RequestError) as exc:
        raise CardPersistenceError("Database did not confirm document save") from exc
    try:
        receipt = DocumentCardsReceipt.model_validate(result.data)
        if (
            receipt.document_id != payload.document_id
            or receipt.payload_hash != payload.payload_hash
            or len(receipt.card_ids) != len(payload.cards)
            or len(set(receipt.card_ids)) != len(receipt.card_ids)
        ):
            raise ValueError("Save receipt does not match the requested cards")
        return receipt.model_dump(mode="json")
    except (ValidationError, ValueError, TypeError) as exc:
        raise CardPersistenceError("Database returned an invalid save receipt") from exc


def _quote(value: str) -> str:
    # or_() 필터 값에 , ( ) 같은 PostgREST 예약문자가 들어가도 깨지지 않도록 큰따옴표로 감싼다
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_search_filter(q: str) -> str:
    # 제목/요약/배경은 부분일치, keywords(text[])는 원소 정확일치만 가능 (PostgREST 제약)
    pattern = _quote(f"*{q}*")
    return ",".join(
        [
            f"title.ilike.{pattern}",
            f"summary.ilike.{pattern}",
            f"context.ilike.{pattern}",
            f"keywords.cs.{{{_quote(q)}}}",
        ]
    )


def _apply_filters(query, q: str):
    # 본 조회와 count 재조회가 같은 조건을 쓰도록 한 곳에 둔다
    # RAG 검색 대상과 같은 기준으로 active 카드만 목록에 노출한다
    query = query.eq("status", "active")
    if q:
        query = query.or_(_build_search_filter(q))
    return query


def _to_card(row: dict) -> dict:
    card_documents = row.pop("card_documents", None) or []
    evidences = [
        {
            "id": evidence["id"],
            "document_id": evidence["document_id"],
            "document_title": evidence["documents"]["title"],
            "original_filename": evidence["documents"]["original_filename"],
            "location": evidence["location"],
            "excerpt": evidence["excerpt"],
            "url": None,
        }
        for evidence in card_documents
    ]
    return {**row, "evidences": evidences}


async def list_cards(q: str, sort: CardSort, page: int, page_size: int) -> dict:
    q = q.strip()
    column, desc = SORT_COLUMNS[sort]
    start = (page - 1) * page_size

    query = (
        _apply_filters(supabase.table("knowledge_cards").select(CARD_SELECT, count="exact"), q)
        .order(column, desc=desc)
        # 정렬 값이 같은 카드끼리 페이지 경계에서 순서가 바뀌지 않도록 id로 한 번 더 정렬
        .order("id")
        .range(start, start + page_size - 1)
    )

    try:
        result = query.execute()
    except APIError as e:
        if e.code != RANGE_NOT_SATISFIABLE:
            raise HTTPException(status_code=500, detail=LIST_FAILED_DETAIL)
        # 마지막 페이지를 넘긴 요청: 빈 목록 + 전체 개수를 돌려준다
        try:
            count_result = _apply_filters(
                supabase.table("knowledge_cards").select("id", count="exact", head=True), q
            ).execute()
        except APIError:
            raise HTTPException(status_code=500, detail=LIST_FAILED_DETAIL)
        return {"items": [], "total": count_result.count or 0}

    return {
        "items": [_to_card(row) for row in result.data],
        "total": result.count or 0,
    }
