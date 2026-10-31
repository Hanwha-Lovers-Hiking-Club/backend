# 지식카드 조회와 검증된 문서의 원자적 저장을 담당한다.
import asyncio
import math

import httpx
from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.db import supabase
from app.domains.documents.pipeline.document_lifecycle import DocumentSaveError
from app.domains.documents.pipeline.processing import DocumentSaveRequest, SaveReceipt
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

EMBEDDING_COLUMNS = {
    "content": "content_embedding",
    "situation": "situation_embedding",
    "solution": "solution_embedding",
    "lesson": "lesson_embedding",
}


async def save_document_cards(request: DocumentSaveRequest) -> SaveReceipt:
    """Persist one document's passed cards, evidence and vectors in one DB transaction.

    The database RPC owns the transaction and the document-scoped idempotency key.
    Its receipt is returned only after all rows and the completion record commit.
    """
    from app.config import settings

    request = DocumentSaveRequest.model_validate(request)
    document_id = request.document.document_id
    if len({card.experience_id for card in request.cards}) != len(request.cards):
        raise DocumentSaveError("Duplicate card experiences")
    vectors_by_experience = {item.experience_id: item for item in request.embeddings}
    if len(vectors_by_experience) != len(request.embeddings):
        raise DocumentSaveError("Duplicate card embeddings")
    if set(vectors_by_experience) - {card.experience_id for card in request.cards}:
        raise DocumentSaveError("Embedding has no matching card")

    rows = []
    for card in request.cards:
        if any(
            ref.document_id != document_id for ref in card.source_extraction.evidence_index.values()
        ):
            raise DocumentSaveError("Card references a different document")
        evidence_ids = dict.fromkeys(
            evidence_id for refs in card.field_evidence_refs.values() for evidence_id in refs
        )
        if not evidence_ids:
            raise DocumentSaveError("Card has no source evidence")
        embedding = vectors_by_experience.get(card.experience_id)
        if embedding is not None and (
            embedding.model != settings.EMBEDDING_MODEL
            or embedding.dimensions != settings.EMBEDDING_DIMENSIONS
            or set(embedding.vectors) != set(embedding.input_hashes)
            or any(
                len(vector) != settings.EMBEDDING_DIMENSIONS
                or not all(type(value) in (int, float) and math.isfinite(value) for value in vector)
                or not any(value != 0 for value in vector)
                for vector in embedding.vectors.values()
            )
        ):
            raise DocumentSaveError("Embedding does not match configured model")
        row = card.to_card_fields()
        row["status"] = "active"
        row["evidences"] = [
            {
                "location": card.source_extraction.evidence_index[evidence_id].location,
                "excerpt": card.source_extraction.evidence_index[evidence_id].excerpt,
            }
            for evidence_id in evidence_ids
        ]
        for view, column in EMBEDDING_COLUMNS.items():
            row[column] = embedding.vectors.get(view) if embedding is not None else None
        rows.append(row)

    params = {
        "p_document_id": str(document_id),
        "p_payload_hash": request.payload_hash,
        "p_cards": rows,
    }
    try:
        result = await asyncio.to_thread(
            lambda: supabase.rpc("save_document_cards", params).execute()
        )
    except (APIError, httpx.RequestError) as exc:
        raise DocumentSaveError("Database did not confirm document save") from exc
    try:
        return SaveReceipt.model_validate(result.data)
    except (ValueError, TypeError) as exc:
        raise DocumentSaveError("Database returned an invalid save receipt") from exc


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
