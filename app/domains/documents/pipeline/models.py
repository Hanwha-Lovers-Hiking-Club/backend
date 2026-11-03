# 노드 사이에서 오가는 모델 (Experience, CardDraft, VerifiedCard, EmbeddedCard 등).
# 노드 간 경계에서 오가는 데이터 모양이므로 state.py와 함께 팀이 합의한 계약이다.
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# extract 노드의 LLM 구조화 출력 타입들. 필드 이름과 순서는 비교 실험
# (scripts/compare_extraction.py)에서 검증한 그대로다. 구조화 출력은 필드 순서대로 생성되므로
# 바꾸면 결과가 달라질 수 있다
EvidenceKind = Literal[
    "observation",
    "hypothesis",
    "rejected_hypothesis",
    "cause",
    "decision",
    "action",
    "result",
    "plan",
]


# 경험의 근거 하나. chunk_id는 LLM 응답에서는 프롬프트용 별칭("D1:3")이고,
# extract 노드가 검사한 뒤 state.Chunk.id로 바꿔서 state에 넣는다
class Evidence(BaseModel):
    kind: EvidenceKind
    statement: str
    chunk_id: str
    quote: str


# extract 노드의 출력 타입. state.IngestionState.experiences에 쌓인다
class Experience(BaseModel):
    title: str
    summary: str
    # 지금은 쓰는 곳이 없다. 검증한 스키마를 바꾸지 않으려고 남겨 둔다
    link_hints: list[str]
    evidences: list[Evidence]


# extract 노드의 LLM structured output 루트 타입. 리스트를 바로 루트로 못 받아서 감싸는 래퍼
class Experiences(BaseModel):
    experiences: list[Experience]


# CardDraft.evidences의 원소 타입
class CardEvidenceDraft(BaseModel):
    document_id: UUID
    location: str | None
    excerpt: str | None
    # 검증 노드가 원문(표의 조건 등)을 다시 보려고 쓰는 값. DB에는 저장하지 않는다.
    # kind는 extract의 근거 종류이고, 문서 머리말에서 온 근거(프로젝트명 등)는 None이다
    chunk_id: str | None = None
    kind: str | None = None


# draft_card 노드의 LLM 구조화 출력 타입들. 필드마다 근거 id(E1, H1 ...)를 먼저 고르고
# 문장을 나중에 쓰게 한다(구조화 출력은 필드 순서대로 생성된다)
class SourcedText(BaseModel):
    source_ids: list[str]
    text: str


class OptionalSourcedText(BaseModel):
    source_ids: list[str]
    text: str | None


class CardComposition(BaseModel):
    # lesson을 근거로 쓸 수 있는지 먼저 판단하게 한다. false면 후보를 만들지 않는다
    lesson_supportable: bool
    lesson_reason: str
    title: SourcedText
    summary: SourcedText
    context: SourcedText
    problem: OptionalSourcedText
    cause: OptionalSourcedText
    approach: OptionalSourcedText
    rationale: OptionalSourcedText
    outcome: OptionalSourcedText
    lesson: SourcedText
    technologies: list[str]
    keywords: list[str]
    project_name: OptionalSourcedText
    department: OptionalSourcedText
    # 원문의 날짜 표현 그대로. 연-월-일이 모두 있고 원문에 있을 때만 draft_card가 date로 바꾼다
    project_date: OptionalSourcedText


# 카드 초안. draft_card 노드가 만들고, 검증 노드가 판정한다
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
    # 필드 이름 -> 그 필드를 뒷받침하는 evidences의 인덱스. 검증 노드가 필드별로 원문을 대조한다.
    # DB에는 저장하지 않는다
    field_sources: dict[str, list[int]] = {}


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
