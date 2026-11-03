# 노드: 질문마다 벡터 검색(KURE-v3, input_type=query)과 BM25 키워드 검색을 실행하고,
# 결과 목록들을 RRF(k=60)로 합쳐 상위 20개를 고른다.
#
# RRF(Reciprocal Rank Fusion)는 점수 대신 순위만 쓴다. 벡터 유사도와 BM25 점수는 범위가 달라서
# 직접 더할 수 없지만, 순위는 같은 기준으로 합칠 수 있다.
import asyncio

from app.domains.baseline_chat import repository
from app.domains.baseline_chat.pipeline.bm25 import BM25Index
from app.domains.baseline_chat.pipeline.state import RetrievalState, RetrievedChunk
from app.domains.documents.pipeline.embedding import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_NAME,
    get_embedding_client,
)

# 질문 1개, 검색 방식 1개마다 가져오는 결과 수
PER_QUERY_COUNT = 20
# RRF 결합 후 재순위 단계로 넘기는 후보 수
CANDIDATE_COUNT = 20
# RRF 논문과 대부분의 구현이 쓰는 기본값. 클수록 상위 순위와 하위 순위의 점수 차이가 줄어든다
RRF_K = 60


def embed_queries(texts: list[str]) -> list[list[float]]:
    # 저장할 때(embed 노드)와 같은 모델/차원이어야 같은 벡터 공간에서 비교된다.
    # 저장할 때는 input_type이 "document"이고, 질문은 "query"로 임베딩한다
    response = get_embedding_client().embeddings.create(
        model=EMBEDDING_MODEL_NAME,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
        extra_body={"input_type": "query"},
    )
    return [item.embedding for item in response.data]


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]], k: int = RRF_K
) -> list[tuple[str, float]]:
    # 목록마다 순위 r(1부터)인 항목에 1 / (k + r)을 더하고, 합이 큰 순서로 돌려준다
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


async def retrieve(state: RetrievalState) -> dict:
    queries = state["queries"]
    # 임베딩 API와 supabase 클라이언트가 동기라서 이벤트 루프를 막지 않도록 스레드에서 돌린다
    chunks, embeddings = await asyncio.gather(
        asyncio.to_thread(repository.get_all_chunks),
        asyncio.to_thread(embed_queries, queries),
    )
    if not chunks:
        return {"candidates": []}

    vector_results = await asyncio.gather(
        *(
            asyncio.to_thread(repository.match_chunks, embedding, PER_QUERY_COUNT)
            for embedding in embeddings
        )
    )
    ranked_lists = [[row["id"] for row in rows] for rows in vector_results]

    bm25 = BM25Index([chunk["search_text"] for chunk in chunks])
    for query in queries:
        ranked_lists.append([chunks[i]["id"] for i, _ in bm25.top_k(query, PER_QUERY_COUNT)])

    chunks_by_id = {chunk["id"]: chunk for chunk in chunks}
    candidates: list[RetrievedChunk] = [
        {**chunks_by_id[chunk_id], "score": score}
        for chunk_id, score in reciprocal_rank_fusion(ranked_lists)
        # 전체 청크를 읽은 뒤 다시 인덱싱되어 사라진 청크는 뺀다
        if chunk_id in chunks_by_id
    ][:CANDIDATE_COUNT]
    return {"candidates": candidates}
