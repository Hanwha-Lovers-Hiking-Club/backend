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


# draft_card 노드가 LLM에게 "이 청크에서 어떤 문장을 근거 발췌문으로 쓸지" 고르게 할 때 쓰는 타입.
# chunk_id가 실제로 존재하는지, excerpt가 그 청크 원문의 부분 문자열인지는 draft_card가 코드로
# 검증한다
class CardExcerptPick(BaseModel):
    chunk_id: str
    excerpt: str


# draft_card 노드의 LLM structured output 타입. problem/cause/approach/rationale/outcome은
# extract 결과를 그대로 복사해 쓰므로 여기서 다시 생성하지 않는다
class CardDraftContent(BaseModel):
    title: str
    summary: str
    context: str
    lesson: str
    technologies: list[str]
    keywords: list[str]
    project_name: str | None
    department: str | None
    # date 타입으로 받거나 YYYY-MM-DD로 쓰게 하면 LLM이 "2024년 3월" 같은 부분 날짜에 일을 지어내서
    # 채운다. 원문의 날짜 표현을 그대로 복사해 오게 하고, draft_card가 원문에 실제로 있고 연월일이
    # 모두 있는 표현만 date로 변환한다
    project_date: str | None
    excerpts: list[CardExcerptPick]


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
    # 구조화 출력은 필드 순서대로 생성되므로, 이유를 먼저 쓰고 판정하게 해서 결론을 정한 뒤
    # 이유를 끼워 맞추는 걸 줄인다
    reason: str
    verdict: Literal["pass", "rewrite", "discard"]


# verify 노드의 출력 타입. state.IngestionState.verified_cards에 쌓인다
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
