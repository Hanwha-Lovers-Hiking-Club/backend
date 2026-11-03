# 노드 사이에서 오가는 카드/임베딩 모델 (CardDraft, VerifiedCard, EmbeddedCard 등).
# 노드 간 경계에서 오가는 데이터 모양이므로 state.py와 함께 팀이 합의한 계약이다.
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


# CardDraft.evidences의 원소 타입
class CardEvidenceDraft(BaseModel):
    document_id: UUID
    location: str | None
    excerpt: str | None


# 카드 초안. 카드 생성 노드(재설계 중)가 만든다
class CardDraft(BaseModel):
    title: str
    summary: str
    context: str
    problem: str | None
    cause: str | None
    approach: str | None
    rationale: str | None
    outcome: str | None
    lesson: str
    technologies: list[str]
    keywords: list[str]
    project_name: str | None
    department: str | None
    project_date: date | None
    # 카드당 근거 1개 이상 (docs/schema.md 제약). DB가 아니라 파이프라인이 보장한다
    evidences: list[CardEvidenceDraft]


# 카드 검증 판정 결과 타입. VerifiedCard 안에 담겨서 쓰인다
class VerificationResult(BaseModel):
    # 구조화 출력은 필드 순서대로 생성되므로, 이유를 먼저 쓰고 판정하게 해서 결론을 정한 뒤
    # 이유를 끼워 맞추는 걸 줄인다
    reason: str
    verdict: Literal["pass", "rewrite", "discard"]


# 카드 검증의 출력 타입. state.IngestionState.verified_cards에 쌓인다
# (embed 노드가 pass만 꺼내 씀)
class VerifiedCard(BaseModel):
    draft: CardDraft
    verification: VerificationResult


# embed 노드가 kure-v3로 만드는 4종 벡터. EmbeddedCard 안에 담겨서 쓰인다
class CardEmbeddings(BaseModel):
    content_embedding: list[float]
    situation_embedding: list[float]
    solution_embedding: list[float]
    lesson_embedding: list[float]


# embed 노드의 출력 타입. state.IngestionState.embedded_cards에 쌓인다 (save 노드가 그대로 저장)
# "이 리스트에 들어있다 = verify를 통과하고 임베딩까지 끝난 카드"라는 뜻이라, verdict나 embeddings의
# 존재 여부를 다시 확인할 필요가 없다
class EmbeddedCard(BaseModel):
    draft: CardDraft
    embeddings: CardEmbeddings


# embed_chunks 노드의 출력 타입. state.IngestionState.embedded_chunks에 쌓인다
# (save_chunks 노드가 저장)
# content는 원문 그대로이고, 임베딩은 location을 앞에 붙인 텍스트로 만든 것이다
class EmbeddedChunk(BaseModel):
    document_id: UUID
    chunk_index: int
    location: str | None
    content: str
    embedding: list[float]
