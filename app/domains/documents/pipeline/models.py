# 노드 사이에서 오가는 모델 (Experience, CardDraft, VerifiedCard, EmbeddedCard 등).
# 노드 간 경계에서 오가는 데이터 모양이므로 state.py와 함께 팀이 합의한 계약이다.
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# extract 노드의 LLM 구조화 출력 타입들. 필드 이름과 순서는 비교 실험에서 검증한 그대로다.
# 구조화 출력은 필드 순서대로 생성되므로 바꾸면 결과가 달라질 수 있다
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


# 원문에서 기술을 확인한 방식. 이름이나 코드로 식별할 수 있는 것만 인정한다
# (컴포넌트 이름·패턴에서 미루어 짐작한 기술은 받지 않는다)
#   name: 원문에 기술 이름이 그대로 있다
#   import: import·require·의존성 선언에 기술 이름이 있다
#   code_language: 코드 블록의 언어 표시(```python 등)
#   syntax: 그 기술에만 있는 문법(CREATE TABLE 등의 SQL 문장 등)
TechnologyBasis = Literal["name", "import", "code_language", "syntax"]


# 경험에서 실제로 사용한 기술 하나와 그 근거. chunk_id는 Evidence와 같은 규칙으로 바뀐다
class TechnologyUse(BaseModel):
    name: str
    basis: TechnologyBasis
    chunk_id: str
    quote: str


# extract 노드의 출력 타입. state.IngestionState.experiences에 쌓인다
class Experience(BaseModel):
    title: str
    summary: str
    # 지금은 쓰는 곳이 없다. 검증한 스키마를 바꾸지 않으려고 남겨 둔다
    link_hints: list[str]
    evidences: list[Evidence]
    # 경험 추출 결과가 달라지지 않도록 evidences 뒤에 둔다.
    # 문서 전체를 보는 단계는 extract뿐이라 여기서 뽑는다
    technologies: list[TechnologyUse]


# extract 노드의 LLM structured output 루트 타입. 리스트를 바로 루트로 못 받아서 감싸는 래퍼
class Experiences(BaseModel):
    experiences: list[Experience]
    # 업로드 묶음 전체의 프로젝트 이름. 경험 추출 결과가 달라지지 않도록 experiences 뒤에 둔다
    project_name: str | None


# 기간의 한쪽 경계가 되는 날짜 하나와 그 근거(project_meta.py가 원문과 대조한다)
class DatedQuote(BaseModel):
    # 날짜가 있는 청크의 id(프롬프트 별칭 "D1:3")
    chunk_id: str
    # 날짜가 적힌 원문 구절 그대로
    quote: str
    # YYYY-MM
    month: str
    # quote에 연도가 없을 때(예: "9/1") 그 날짜의 연도를 알려 주는 같은 문서의 구절
    # (제목, 표 머리글, 문단). quote에 연도가 있으면 null
    year_quote: str | None


# 프로젝트 소개 정보의 LLM 구조화 출력 타입(project_meta.py)
class ProjectMetaDraft(BaseModel):
    # 같은 문서의 분류가 실행마다 미분류와 오가지 않도록 null을 허용하지 않는다
    domain: Literal["ai", "frontend", "backend", "data", "app"]
    team: str | None
    # 문서에 명시된 프로젝트 기간(예정 기간 제외)의 시작과 종료
    stated_start: DatedQuote | None
    stated_end: DatedQuote | None
    # 문서에 기록된 이 프로젝트의 첫 활동 날짜와 마지막 활동 날짜.
    # 활동 날짜가 하나뿐이면 같은 날짜를 양쪽에 쓴다
    first_activity: DatedQuote | None
    last_activity: DatedQuote | None
    description: str | None


# CardDraft.evidences의 원소 타입
class CardEvidenceDraft(BaseModel):
    document_id: UUID
    location: str | None
    excerpt: str | None
    # 검증 노드가 원문(표의 조건 등)을 다시 보려고 쓰는 값. DB에는 저장하지 않는다.
    # kind는 extract의 근거 종류이고, 문서 머리말에서 온 근거(부서명 등)는 None이다
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
    # 기술은 여기서 쓰지 않는다. extract가 근거와 함께 뽑은 경험의 기술을 draft_card가 그대로 쓴다
    keywords: list[str]
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
    department: str | None
    project_date: date | None
    # 카드당 근거 1개 이상 (docs/schema.md 제약). DB가 아니라 파이프라인이 보장한다
    evidences: list[CardEvidenceDraft]
    # 필드 이름 -> 그 필드를 뒷받침하는 evidences의 인덱스. 검증 노드가 필드별로 원문을 대조한다.
    # DB에는 저장하지 않는다
    field_sources: dict[str, list[int]] = {}
    # 기술(technologies의 표준 ID) -> 그 기술을 확인한 원문 근거. 검증 노드가 기술마다 대조한다.
    # 카드 근거(evidences)와 따로 둔다: 코드 블록 표시 같은 기술 근거는 화면의 근거 문서로 보여 줄
    # 내용이 아니다. DB에는 저장하지 않는다
    technology_sources: dict[str, list[CardEvidenceDraft]] = {}
    # 이 카드를 만든 경험의 state.experiences 인덱스. 검증 노드가 경험의 근거 전체를 보려고 쓴다
    experience_index: int | None = None


# verify 노드의 LLM 구조화 출력 타입. 카드의 주장 하나와 그 판정
ClaimJudgement = Literal[
    "supported",
    "condition_dropped",
    "overstated",
    "not_a_result",
    "result_unconfirmed",
    "rejected_as_adopted",
    "not_in_evidence",
    "lesson_within_scope",
    "lesson_overreach",
]


class ClaimCheck(BaseModel):
    # 주장 -> 대조한 근거 -> 이유 -> 판정 순으로 쓰게 해서 결론부터 정하는 걸 줄인다
    field: str
    claim: str
    evidence_ids: list[str]
    note: str
    judgement: ClaimJudgement


ReuseKind = Literal["decision_criteria", "action", "diagnosis", "none"]


# 다른 프로젝트에서 쓸 수 있는 경험 지식인지 본다. "재사용할 수 있는가" 같은 넓은 질문은 LLM이 거의
# 항상 통과시켜서, 적용 상황·행동·확인 범위를 문구로 먼저 뽑은 뒤 유형을 고르게 한다
class ReuseCheck(BaseModel):
    situation: str
    guidance: str
    confirmed_scope: str
    kind: ReuseKind


# 쓰인 주장만 대조하면 빠뜨린 내용을 못 잡는다. 자유 서술로 받으면 사소한 내용까지 잡혀서
# 근거 id를 먼저 고르게 하고, 코드가 결과(result) 근거를 지목한 것만 인정한다
class OmittedLimit(BaseModel):
    evidence_id: str
    limit: str


# 기술은 주장 문장이 아니라 항목이라 따로 판정한다. 기술마다 id(K1..)로 판정해야 어느 기술을 뺄지
# 정할 수 있고, 판정이 빠진 기술도 찾을 수 있다
TechnologyJudgement = Literal[
    "supported",
    "rejected_as_adopted",
    "not_in_evidence",
    "inferred_only",
    "not_a_technology",
]


class TechnologyCheck(BaseModel):
    technology_id: str
    note: str
    judgement: TechnologyJudgement


class ClaimChecks(BaseModel):
    checks: list[ClaimCheck]
    omitted_limits: list[OmittedLimit]
    reuse: ReuseCheck
    # 기존 판정 결과가 달라지지 않도록 맨 뒤에 둔다
    technology_checks: list[TechnologyCheck]


# 카드 검증 결과. 검증 판정(verdict)과 공개 상태(status)는 따로 기록한다
# (재사용 가치가 없는 카드는 내용이 정확하면 passed지만 공개 정책상 draft다).
#   passed: 모든 주장이 근거로 지지됨(문제 있던 조건부·선택 필드는 비운 뒤). 재사용 가치가 부족하면
#           내용은 맞으므로 passed지만 공개 상태는 draft다
#   held: 필수 필드(title/summary/context/lesson)에 근거를 벗어난 주장이 있거나, 결론을 바꾸는
#         한계를 카드가 빠뜨림 -> 사람 검토
#   excluded: 문제 필드를 비우고 나니 카드 구조 조건이 깨짐 -> 저장 안 함
#   incomplete: 호출 실패, 응답 파싱 실패, 값이 있는 필드의 판정 누락 -> 저장 안 함
class VerificationResult(BaseModel):
    verdict: Literal["passed", "held", "excluded", "incomplete"]
    reasons: list[str]
    cleared_fields: list[str] = []
    # 저장할 때의 knowledge_cards.status. excluded/incomplete는 저장하지 않으므로 None
    status: Literal["active", "draft"] | None = None


# verify 노드의 출력 타입. state.IngestionState.verified_cards에 쌓인다
# (embed 노드가 status가 있는 것만 꺼내 씀). draft는 문제 필드를 비운 뒤의 카드다
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
# "이 리스트에 들어있다 = 저장 대상이고 임베딩까지 끝난 카드"라는 뜻이라, verdict나 embeddings의
# 존재 여부를 다시 확인할 필요가 없다. status는 verify가 정한 공개 상태다
class EmbeddedCard(BaseModel):
    draft: CardDraft
    # draft 카드는 임베딩하지 않는다(None으로 저장 -> 벡터 검색에 안 잡힘)
    embeddings: CardEmbeddings | None
    status: Literal["active", "draft"] = "draft"
    draft_reasons: list[str] = []


# embed_chunks 노드의 출력 타입. state.IngestionState.embedded_chunks에 쌓인다
# (save_chunks 노드가 저장)
# content는 원문 그대로이고, 임베딩은 location을 앞에 붙인 텍스트로 만든 것이다
class EmbeddedChunk(BaseModel):
    document_id: UUID
    chunk_index: int
    location: str | None
    content: str
    embedding: list[float]
