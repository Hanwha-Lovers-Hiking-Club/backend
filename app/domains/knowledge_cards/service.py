# 지식카드 로직: Supabase 조회와 DB 행 -> 응답 모양 변환.
from app.db import supabase
from app.domains.knowledge_cards.schemas import CardSort

# 근거(card_documents)와 근거 문서(documents)를 한 번에 가져온다
CARD_SELECT = (
    "*, card_documents(id, document_id, location, excerpt, documents(title, original_filename))"
)

# (정렬 컬럼, 내림차순 여부)
SORT_COLUMNS: dict[str, tuple[str, bool]] = {
    "recent": ("updated_at", True),
    "created": ("created_at", True),
    "name": ("title", False),
}


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
        supabase.table("knowledge_cards")
        .select(CARD_SELECT, count="exact")
        # RAG 검색 대상과 같은 기준으로 active 카드만 목록에 노출한다
        .eq("status", "active")
    )
    if q:
        query = query.or_(_build_search_filter(q))

    result = (
        query.order(column, desc=desc)
        # 정렬 값이 같은 카드끼리 페이지 경계에서 순서가 바뀌지 않도록 id로 한 번 더 정렬
        .order("id")
        .range(start, start + page_size - 1)
        .execute()
    )

    return {
        "items": [_to_card(row) for row in result.data],
        "total": result.count or 0,
    }
