# 노드: 재순위 모델 API로 후보 청크 20개 중 상위 5개를 고른다 (공급자는 추후 결정).
# RERANK_API_KEY가 없으면 RRF 순위 상위 5개를 그대로 쓰고 경고 로그를 남긴다.
#
# 공급자가 정해지면 call_rerank_api 함수 하나만 구현하면 된다.
import logging

from app.config import settings
from app.domains.baseline_chat.pipeline.state import RetrievalState, RetrievedChunk

logger = logging.getLogger(__name__)

TOP_N = 5


async def call_rerank_api(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    # 질문과 문서 목록을 보내고, (documents에서의 위치, 관련도 점수)를 관련도가 높은 순서로
    # top_n개 받는다. API 키는 settings.RERANK_API_KEY를 쓴다
    raise NotImplementedError("재순위 모델 공급자가 정해지면 구현한다")


async def rerank(state: RetrievalState) -> dict:
    candidates = state["candidates"]
    if not candidates:
        return {"reranked": []}

    if not settings.RERANK_API_KEY:
        logger.warning("RERANK_API_KEY가 없어서 재순위 없이 RRF 순위 상위 %d개를 씁니다", TOP_N)
        return {"reranked": candidates[:TOP_N]}

    # 재순위 기준은 대화 내역을 반영한 독립 질문이다. 대상은 문맥 설명이 붙은 검색용 텍스트다
    results = await call_rerank_api(
        state["queries"][0], [chunk["search_text"] for chunk in candidates], TOP_N
    )
    reranked: list[RetrievedChunk] = [
        {**candidates[index], "score": score} for index, score in results[:TOP_N]
    ]
    return {"reranked": reranked}
