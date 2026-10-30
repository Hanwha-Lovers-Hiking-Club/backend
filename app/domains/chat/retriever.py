# 2단계: 지식카드 검색. 질문을 임베딩해 의도에 맞는 임베딩 종류로 유사 카드를 찾고,
# 전체(content) 임베딩 결과와 합쳐 후보를 추린다.
#
# 따로 리랭커 모델은 두지 않았다. 후보를 MAX_CANDIDATES로 자르고, 정밀한 선별은 다음 단계의
# 검증 에이전트(verifier.py)가 맡는다.
import asyncio

from app.db import supabase
from app.domains.chat.models import Candidate, Intent, IntentKind
from app.domains.documents.pipeline.embedding import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_NAME,
    get_embedding_client,
)
from app.domains.knowledge_cards.schemas import KnowledgeCard
from app.domains.knowledge_cards.service import get_active_cards_by_ids

# match_knowledge_cards의 embedding_kind 값 (sql/functions.sql)
EMBEDDING_KIND_BY_INTENT: dict[IntentKind, str] = {
    "similar_project": "content",
    "similar_issue": "situation",
    "solution": "solution",
    "similar_lesson": "lesson",
    "tech_stack": "situation",
    "general": "content",
    "off_topic": "content",
}

# 임베딩 종류별로 DB에서 가져올 개수. 두 종류를 합치면 중복이 생겨서 MAX_CANDIDATES보다 넉넉히
# 받는다
MATCH_COUNT = 8
MAX_CANDIDATES = 5
# 이보다 유사도가 낮은 카드는 후보에서 뺀다. 애매한 구간은 verifier.py가 거른다.
# ponytail: 실제 kure-v3로 샘플 카드 4개를 재본 값이다(2026-10-30). 관련 있는 카드의 전체(content)
# 임베딩 유사도는 0.32~0.65, 무관한 질문은 최대 0.05, 다른 주제 카드는 0.01~0.1이었다. 0.3이면
# 0.32짜리 정답이 아슬아슬해서 0.2로 잡았다. 샘플이 적으니 실제 카드가 쌓이면 다시 찍어 본다.
# 너무 높으면 "찾지 못했다"가 잦아지고, 너무 낮으면 검증 단계 부담이 는다
MIN_SIMILARITY = 0.2


def _embed_query(text: str) -> list[float]:
    # 인제스트의 embed_documents와 같은 모델/차원이어야 같은 벡터 공간에서 비교된다.
    # 저장할 때는 input_type이 "document"이고, 질문은 "query"로 임베딩한다
    response = get_embedding_client().embeddings.create(
        model=EMBEDDING_MODEL_NAME,
        input=[text],
        dimensions=EMBEDDING_DIMENSIONS,
        extra_body={"input_type": "query"},
    )
    return response.data[0].embedding


def _match(embedding: list[float], kind: str) -> list[dict]:
    result = supabase.rpc(
        "match_knowledge_cards",
        {
            "query_embedding": embedding,
            "embedding_kind": kind,
            "match_count": MATCH_COUNT,
            "min_similarity": MIN_SIMILARITY,
        },
    ).execute()
    return result.data or []


def _search(query: str, kind: str) -> list[Candidate]:
    embedding = _embed_query(query)

    # 의도에 맞는 임베딩과 전체 임베딩 둘 다 찾아서, 카드마다 더 높은 유사도를 쓴다
    best: dict[str, float] = {}
    for embedding_kind in dict.fromkeys([kind, "content"]):
        for row in _match(embedding, embedding_kind):
            best[row["id"]] = max(best.get(row["id"], 0.0), row["similarity"])

    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)[:MAX_CANDIDATES]
    cards = get_active_cards_by_ids([card_id for card_id, _ in ranked])
    # 조회 사이에 카드가 사라지거나 active가 아니게 됐을 수 있어서 있는 것만 남긴다
    return [
        Candidate(card=KnowledgeCard.model_validate(cards[card_id]), similarity=similarity)
        for card_id, similarity in ranked
        if card_id in cards
    ]


async def search_cards(intent: Intent) -> list[Candidate]:
    # 임베딩 API와 supabase 클라이언트가 동기라서 이벤트 루프를 막지 않도록 스레드에서 돌린다
    return await asyncio.to_thread(
        _search, intent.search_query, EMBEDDING_KIND_BY_INTENT[intent.kind]
    )
