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


async def list_cards(sort: CardSort, page: int, page_size: int) -> dict:
    column, desc = SORT_COLUMNS[sort]
    start = (page - 1) * page_size

    result = (
        supabase.table("knowledge_cards")
        .select(CARD_SELECT, count="exact")
        # RAG 검색 대상과 같은 기준으로 active 카드만 목록에 노출한다
        .eq("status", "active")
        .order(column, desc=desc)
        # 정렬 값이 같은 카드끼리 페이지 경계에서 순서가 바뀌지 않도록 id로 한 번 더 정렬
        .order("id")
        .range(start, start + page_size - 1)
        .execute()
    )

    return {
        "items": [_to_card(row) for row in result.data],
        "total": result.count or 0,
    }
