# LLM 구조화 출력 모델 (ExtractedElements, CardDraft, VerificationResult 등).
# 노드 간 경계에서 오가는 데이터 모양이므로 state.py와 함께 팀이 합의한 계약이다.
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


# extract 노드의 출력 타입. state.IngestionState.extracted_elements에 쌓인다
class ExtractedElements(BaseModel):
    # knowledge_cards의 조건부 5개 필드와 1:1. 전부 비어 있으면 draft_card가 카드를 만들지 않는다
    problem: str | None
    cause: str | None
    approach: str | None
    rationale: str | None
    outcome: str | None
    # 이 요소를 뽑아낸 청크 id 목록(state.Chunk.id). draft_card가 근거(location/excerpt) 연결에 쓴다
    source_chunk_ids: list[str]


# extract 노드의 LLM structured output 루트 타입. 리스트를 바로 루트로 못 받아서 감싸는 래퍼
class ExtractedElementsBatch(BaseModel):
    elements: list[ExtractedElements]


# CardDraft.evidences의 원소 타입. draft_card 노드가 채운다
class CardEvidenceDraft(BaseModel):
    document_id: UUID
    location: str | None
    excerpt: str | None


# draft_card 노드의 출력 타입. state.IngestionState.card_drafts에 쌓인다
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
    # 카드당 근거 1개 이상 (docs/schema.md 제약). DB가 아니라 draft_card가 여기서 보장한다
    evidences: list[CardEvidenceDraft]


# verify 노드의 판정 결과 타입. VerifiedCard 안에 담겨서 쓰인다
class VerificationResult(BaseModel):
    verdict: Literal["pass", "rewrite", "discard"]
    reason: str


# verify 노드의 출력 타입. state.IngestionState.verified_cards에 쌓인다 (save 노드가 pass만 저장)
class VerifiedCard(BaseModel):
    draft: CardDraft
    verification: VerificationResult
