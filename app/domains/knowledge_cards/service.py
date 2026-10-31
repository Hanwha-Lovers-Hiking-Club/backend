# 지식카드 로직: Supabase 조회/저장과 DB 행 -> 응답 모양 변환.
from fastapi import HTTPException
from postgrest.exceptions import APIError

from app.core.technology import display_name, find_technologies, is_valid_id, normalize_technology
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

# PostgREST: 요청한 범위가 전체 결과 수를 넘어선 경우의 에러 코드
RANGE_NOT_SATISFIABLE = "PGRST103"

LIST_FAILED_DETAIL = "지식카드 목록을 조회하지 못했습니다."


def _quote(value: str) -> str:
    # or_() 필터 값에 , ( ) 같은 PostgREST 예약문자가 들어가도 깨지지 않도록 큰따옴표로 감싼다
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# 한 검색어에서 기술 조건으로 바꿀 최대 개수(긴 문장을 넣어도 필터가 커지지 않게)
MAX_TECHNOLOGY_CONDITIONS = 10


def _technology_ids(q: str) -> list[str]:
    # 입력에서 기술 표준 ID를 뽑는다. 사전에서 찾은 것("넥스트에서" -> nextjs)에 더해,
    # 각 단어도 정규화해서 사전에 없는 기술도 같은 철자면 맞게 한다(저장할 때도 같은 규칙을 거쳤다).
    # technologies에는 표준 ID가 저장되어 있어서 입력을 그대로 비교하면 "넥스트"로는 못 찾는다.
    ids = find_technologies(q)
    for token in q.split():
        tech_id = normalize_technology(token)
        if tech_id and is_valid_id(tech_id) and tech_id not in ids:
            ids.append(tech_id)
    return ids[:MAX_TECHNOLOGY_CONDITIONS]


def _build_search_filter(q: str) -> str:
    # 제목/요약/배경은 부분일치, keywords/technologies(text[])는 원소 정확일치만 가능
    # (PostgREST 제약). technologies는 정규화한 표준 ID로 비교한다
    pattern = _quote(f"*{q}*")
    return ",".join(
        [
            f"title.ilike.{pattern}",
            f"summary.ilike.{pattern}",
            f"context.ilike.{pattern}",
            f"keywords.cs.{{{_quote(q)}}}",
            *(f"technologies.cs.{{{_quote(tech_id)}}}" for tech_id in _technology_ids(q)),
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
    # technologies에는 표준 ID가 저장되어 있어서 화면에는 공식 표기로 내려준다
    technologies = [display_name(t) for t in row.get("technologies") or []]
    return {**row, "technologies": technologies, "evidences": evidences}


def create_card(card: dict, evidences: list[dict]) -> str:
    # documents 도메인(save 노드)이 이 함수를 거쳐서만 knowledge_cards/card_documents에 쓴다.
    # card/evidences는 다른 도메인의 pydantic 모델이 아니라 plain dict로 받는다 — 두 도메인이
    # 서로의 내부 타입을 몰라도 되게 하기 위함
    result = supabase.table("knowledge_cards").insert(card).execute()
    card_id = result.data[0]["id"]
    if evidences:
        rows = [{**evidence, "card_id": card_id} for evidence in evidences]
        supabase.table("card_documents").insert(rows).execute()
    return card_id


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
