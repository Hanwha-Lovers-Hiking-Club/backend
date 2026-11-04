# 원문(document_chunks) 검색. 벡터 검색과 BM25 키워드 검색을 RRF로 합쳐 후보 청크를 추린다.
#
# 벡터는 표현이 달라도 뜻이 비슷한 청크(optimistic update <-> 낙관적 업데이트)를 잘 찾고,
# BM25는 정확한 표기(FE-422, settlement_ledger)를 잘 찾는다. 키워드 검색어에는 질문에 나온 기술의
# 모든 표기(WebSocket -> 웹소켓)를 붙인다. 원문에는 정규화 전 표기가 그대로 있기 때문이다.
# 후보가 질문에 실제로 답이 되는지는 다음 단계의 검증 에이전트가 판단한다.
import asyncio
import time

from app.core.technology import aliases_of, find_technologies
from app.db import supabase
from app.domains.chat.keyword_search import BM25Index
from app.domains.chat.models import ChunkCandidate

# 검색 방식 하나마다 가져오는 결과 수
VECTOR_COUNT = 20
KEYWORD_COUNT = 20
# RRF로 합친 뒤 검증 단계로 넘기는 후보 수
MAX_CHUNK_CANDIDATES = 8
# 이보다 유사도가 낮은 청크는 벡터 결과에서 뺀다. 거르는 일은 검증 단계가 맡아서 낮게 둔다.
# 실제 데이터(청크 585개, 2026-11-04)로 잰 값: 자료가 있는 질문의 최고 유사도 0.32~0.59,
# 자료가 없는 개발 질문 0.15~0.28, 개발과 무관한 질문 0.06~0.12. 두 구간이 가까워서 이 값으로
# "자료 없음"을 판단하면 안 되고, 무관한 질문의 청크만 걸러지도록 잡았다
CHUNK_MIN_SIMILARITY = 0.15
# RRF 논문과 대부분의 구현이 쓰는 기본값. 클수록 상위 순위와 하위 순위의 점수 차이가 줄어든다
RRF_K = 60
# BM25용 전체 청크를 다시 읽는 주기. 문서가 새로 올라오면 이 시간 안에 키워드 검색에 반영된다
CORPUS_TTL_SECONDS = 300
# Supabase(PostgREST)는 요청 1번에 최대 1,000행만 돌려주므로 나눠서 읽는다
PAGE_SIZE = 1000


class _Corpus:
    # BM25 인덱스와 청크 조회용 사전. 질문마다 전체 청크를 읽지 않도록 잠깐 들고 있는다
    def __init__(self, chunks: list[dict], titles: dict[str, str]):
        self.chunks = chunks
        self.titles = titles
        self.by_id = {chunk["id"]: chunk for chunk in chunks}
        self.by_position = {(chunk["document_id"], chunk["chunk_index"]): chunk for chunk in chunks}
        # 문서 제목에 이슈 번호·문서 번호(PAY-310, ADR-012)가 많아서 제목과 위치도 함께 색인한다
        self.index = BM25Index([self._search_text(chunk) for chunk in chunks])
        self.loaded_at = time.monotonic()

    def _search_text(self, chunk: dict) -> str:
        title = self.titles.get(chunk["document_id"], "")
        return f"{title}\n{chunk['location'] or ''}\n{chunk['content']}"


_corpus: _Corpus | None = None


def _load_chunks() -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        page = (
            supabase.table("document_chunks")
            .select("id,document_id,chunk_index,location,content")
            .order("id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def _load_titles() -> dict[str, str]:
    rows = supabase.table("documents").select("id,title").execute().data
    return {row["id"]: row["title"] for row in rows}


def _get_corpus(required_ids: list[str]) -> _Corpus:
    # 오래됐거나, 벡터 검색이 찾은 청크가 없으면(방금 업로드된 문서) 다시 읽는다
    global _corpus
    if (
        _corpus is None
        or time.monotonic() - _corpus.loaded_at > CORPUS_TTL_SECONDS
        or any(chunk_id not in _corpus.by_id for chunk_id in required_ids)
    ):
        _corpus = _Corpus(_load_chunks(), _load_titles())
    return _corpus


def _match(embedding: list[float]) -> list[dict]:
    result = supabase.rpc(
        "match_document_chunks",
        {
            "query_embedding": embedding,
            "match_count": VECTOR_COUNT,
            "min_similarity": CHUNK_MIN_SIMILARITY,
        },
    ).execute()
    return result.data or []


def keyword_query(query: str, terms: list[str] | tuple[str, ...] = ()) -> str:
    # 질문 분석이 뽑은 핵심 용어(다른 표기 포함)와, 질문·용어에 나온 기술의 사전 별칭을 덧붙인다.
    # 사전은 기술명만 다루고, 개념의 다른 표기(optimistic update <-> 낙관적 업데이트)는 terms가
    # 맡는다. 같은 표기가 여러 번 들어가도 BM25가 한 번만 센다
    text = " ".join([query, *terms])
    aliases = [alias for tech_id in find_technologies(text) for alias in aliases_of(tech_id)]
    return " ".join([text, *aliases])


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]], k: int = RRF_K
) -> list[tuple[str, float]]:
    # 목록마다 순위 r(1부터)인 항목에 1 / (k + r)을 더하고, 합이 큰 순서로 돌려준다.
    # 벡터 유사도와 BM25 점수는 범위가 달라서 직접 더할 수 없지만, 순위는 같은 기준으로 합칠 수 있다
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _to_candidate(corpus: _Corpus, chunk: dict, score: float, similarity: float | None):
    before = corpus.by_position.get((chunk["document_id"], chunk["chunk_index"] - 1))
    after = corpus.by_position.get((chunk["document_id"], chunk["chunk_index"] + 1))
    return ChunkCandidate(
        id=chunk["id"],
        document_id=chunk["document_id"],
        document_title=corpus.titles.get(chunk["document_id"], ""),
        chunk_index=chunk["chunk_index"],
        location=chunk["location"],
        content=chunk["content"],
        before=before["content"] if before else None,
        after=after["content"] if after else None,
        score=score,
        similarity=similarity,
    )


def _search_chunks(
    query: str, embedding: list[float], terms: list[str] | tuple[str, ...] = ()
) -> list[ChunkCandidate]:
    vector_rows = _match(embedding)
    similarities = {row["id"]: row["similarity"] for row in vector_rows}
    corpus = _get_corpus(list(similarities))
    if not corpus.chunks:
        return []

    keyword_ids = [
        corpus.chunks[i]["id"]
        for i, _ in corpus.index.top_k(keyword_query(query, terms), KEYWORD_COUNT)
    ]
    fused = reciprocal_rank_fusion([list(similarities), keyword_ids])
    return [
        _to_candidate(corpus, corpus.by_id[chunk_id], score, similarities.get(chunk_id))
        for chunk_id, score in fused
        # 다시 읽은 뒤에도 없는 청크(그 사이 문서가 삭제됨)는 뺀다
        if chunk_id in corpus.by_id
    ][:MAX_CHUNK_CANDIDATES]


async def search_chunks(
    query: str, embedding: list[float], terms: list[str] | tuple[str, ...] = ()
) -> list[ChunkCandidate]:
    # supabase 클라이언트가 동기라서 이벤트 루프를 막지 않도록 스레드에서 돌린다
    return await asyncio.to_thread(_search_chunks, query, embedding, terms)
