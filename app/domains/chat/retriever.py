# 2단계: 검색. 질문을 한 번 임베딩해 지식카드와 원문 청크(chunk_retriever.py)를 함께 찾고,
# 카드의 근거 발췌문이 들어 있는 원문 청크로 둘을 연결한다.
#
# 카드는 의도에 맞는 임베딩 종류와 전체(content) 임베딩으로 찾는다. 여기에 원문 후보 청크를 근거로
# 쓰는 카드를 더한다. 원문이 질문과 맞고 그 원문에서 나온 카드라면, 카드 문장이 질문과 덜 비슷해도
# 관련 있을 가능성이 높기 때문이다. 따로 리랭커 모델은 두지 않았고, 정밀한 선별은 다음 단계의
# 검증 에이전트(verifier.py)가 맡는다.
import asyncio
import re

from app.db import supabase
from app.domains.chat.chunk_retriever import search_chunks
from app.domains.chat.models import Candidate, ChunkCandidate, Intent, IntentKind, Retrieval
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
MAX_CANDIDATES = 6
# 이보다 유사도가 낮은 카드는 후보에서 뺀다. 거르는 일은 verifier.py가 맡아서 낮게 둔다.
# 실제 데이터(active 카드 40개, 2026-11-04)로 잰 값: 기대 카드의 유사도는 0.25~0.66이고 대부분
# 1위였다. 자료가 없는 개발 질문도 최고 0.20~0.23이 나와서, 이 값으로 "자료 없음"을 판단할 수 없다.
# 개발과 무관한 질문(0.05~0.10)만 걸러지도록 잡았다
MIN_SIMILARITY = 0.15
# 원문 후보 청크와 연결된 카드의 순위를 올리는 값. 연결로만 찾은 카드는 이 값이 점수가 된다.
# 유사도(0.15~0.7)와 같은 척도라서, 연결된 카드가 비슷한 유사도의 카드보다 한 단계 앞에 오는 정도다
LINK_BONUS = 0.1
# 발췌문 앞부분만으로도 같은 위치라고 볼 수 있는 길이. 원문 변환 차이로 발췌문 끝이 청크와
# 조금 다를 때를 위한 것이다(실측: 발췌문 527개 중 90%가 통째로, 6%가 앞 30자만 일치)
EXCERPT_PREFIX = 30

_SPACES = re.compile(r"\s+")


def embed_query(text: str) -> list[float]:
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


def _vector_similarities(embedding: list[float], kind: str) -> dict[str, float]:
    # 의도에 맞는 임베딩과 전체 임베딩 둘 다 찾아서, 카드마다 더 높은 유사도를 쓴다
    best: dict[str, float] = {}
    for embedding_kind in dict.fromkeys([kind, "content"]):
        for row in _match(embedding, embedding_kind):
            best[row["id"]] = max(best.get(row["id"], 0.0), row["similarity"])
    return best


def _evidences(document_ids: list[str]) -> list[dict]:
    # 원문 후보 청크가 속한 문서를 근거로 쓰는 카드의 발췌문. active 여부는 카드 조회에서 거른다
    if not document_ids:
        return []
    result = (
        supabase.table("card_documents")
        .select("card_id,document_id,excerpt")
        .in_("document_id", document_ids)
        .execute()
    )
    return result.data or []


def _contains_excerpt(content: str, excerpt: str) -> bool:
    # 원문 변환 과정에서 줄바꿈·공백이 달라질 수 있어서 공백을 지우고 비교한다
    content, excerpt = _SPACES.sub("", content), _SPACES.sub("", excerpt)
    if not excerpt:
        return False
    return excerpt in content or (
        len(excerpt) >= EXCERPT_PREFIX and excerpt[:EXCERPT_PREFIX] in content
    )


def link_cards_to_chunks(chunks: list[ChunkCandidate]) -> dict[str, list[str]]:
    # 카드 id -> 그 카드의 근거 발췌문이 들어 있는 원문 후보 청크 id들 (청크 순위 순)
    links: dict[str, list[str]] = {}
    evidences = _evidences(list(dict.fromkeys(chunk.document_id for chunk in chunks)))
    for chunk in chunks:
        for evidence in evidences:
            if evidence["document_id"] != chunk.document_id or not evidence["excerpt"]:
                continue
            if _contains_excerpt(chunk.content, evidence["excerpt"]):
                chunk_ids = links.setdefault(evidence["card_id"], [])
                if chunk.id not in chunk_ids:
                    chunk_ids.append(chunk.id)
    return links


def _rank_cards(similarities: dict[str, float], chunks: list[ChunkCandidate]) -> list[Candidate]:
    # 벡터 검색으로도 찾은 청크에서만 카드를 연결한다. 키워드로만 찾은 청크는 "어떻게", "추천" 같은
    # 흔한 말로 걸린 경우가 있어서(실측: 개발과 무관한 질문에서도 후보 청크가 생김), 거기서 카드까지
    # 끌어오면 잡음이 커진다. 그런 청크도 원문 후보로는 남아서 검증 단계가 판단한다
    links = link_cards_to_chunks([chunk for chunk in chunks if chunk.similarity is not None])
    scores = {
        card_id: similarities.get(card_id, 0.0) + (LINK_BONUS if card_id in links else 0.0)
        for card_id in {*similarities, *links}
    }
    # 연결된 카드 중에는 draft 카드도 있어서, 먼저 active 카드만 조회한 뒤 순위를 자른다
    cards = get_active_cards_by_ids(list(scores))
    ranked = sorted(
        (card_id for card_id in scores if card_id in cards),
        key=lambda card_id: scores[card_id],
        reverse=True,
    )[:MAX_CANDIDATES]
    return [
        Candidate(
            card=KnowledgeCard.model_validate(cards[card_id]),
            similarity=similarities.get(card_id),
            linked_chunk_ids=links.get(card_id, []),
        )
        for card_id in ranked
    ]


async def search(intent: Intent) -> Retrieval:
    # 임베딩 API와 supabase 클라이언트가 동기라서 이벤트 루프를 막지 않도록 스레드에서 돌린다.
    # 질문 임베딩은 한 번만 만들어 카드와 원문 검색이 같이 쓴다
    embedding = await asyncio.to_thread(embed_query, intent.search_query)
    similarities, chunks = await asyncio.gather(
        asyncio.to_thread(_vector_similarities, embedding, EMBEDDING_KIND_BY_INTENT[intent.kind]),
        search_chunks(intent.search_query, embedding),
    )
    cards = await asyncio.to_thread(_rank_cards, similarities, chunks)
    return Retrieval(cards=cards, chunks=chunks)


async def search_cards(intent: Intent) -> list[Candidate]:
    # 아직 원문을 쓰지 않는 기존 흐름(service.py)용. 원문 결합 흐름으로 바뀌면 search로 대체한다
    return (await search(intent)).cards
