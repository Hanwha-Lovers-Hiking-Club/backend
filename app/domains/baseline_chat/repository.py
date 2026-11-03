# 기본 RAG 챗봇 도메인의 DB 호출을 모은다.
# 벡터 검색 rpc, BM25용 전체 청크 조회, 앞뒤 청크 조회
from app.db import supabase

CHUNK_COLUMNS = "id, document_id, chunk_index, location, content, search_text"
# Supabase(PostgREST)는 요청 1번에 최대 1,000행만 돌려주므로 나눠서 읽는다
PAGE_SIZE = 1000


def match_chunks(embedding: list[float], match_count: int) -> list[dict]:
    # sql/baseline_rag.sql의 match_baseline_chunks. id와 유사도만 돌려준다
    result = supabase.rpc(
        "match_baseline_chunks",
        {"query_embedding": embedding, "match_count": match_count},
    ).execute()
    return result.data or []


def get_all_chunks() -> list[dict]:
    # BM25 인덱스를 만들기 위해 전체 청크를 읽는다. 임베딩은 필요 없어서 빼고 읽는다
    rows: list[dict] = []
    start = 0
    while True:
        result = (
            supabase.table("baseline_chunks")
            .select(CHUNK_COLUMNS)
            .order("id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(result.data)
        if len(result.data) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def get_chunks_by_index(document_id: str, chunk_indexes: list[int]) -> list[dict]:
    # 한 문서 안에서 지정한 순번의 청크만 읽는다. 없는 순번(문서의 처음/끝 바깥)은 결과에서 빠진다
    result = (
        supabase.table("baseline_chunks")
        .select("chunk_index, content")
        .eq("document_id", document_id)
        .in_("chunk_index", chunk_indexes)
        .execute()
    )
    return result.data
