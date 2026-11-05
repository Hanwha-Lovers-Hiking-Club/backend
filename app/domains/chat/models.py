# 챗봇 내부에서만 쓰는 모델. LLM 구조화 출력(의도 파악/검증)과 검색 후보를 담는다.
# API 요청/응답 모양이 아니라서 schemas.py가 아니라 여기에 둔다.
from typing import Literal

from pydantic import BaseModel, Field

from app.domains.knowledge_cards.schemas import KnowledgeCard

# 명세서의 의도 예시(유사 프로젝트 / 비슷한 이슈 / 비슷한 교훈 / 비슷한 기술 스택 이슈)에
# "해결 방법"과 "개념 설명"을 더했다. 개발 도메인과 무관한 질문인지는 의도가 아니라
# Intent.is_dev_domain으로 따로 판단한다
IntentKind = Literal[
    "similar_project",
    "similar_issue",
    "solution",
    "similar_lesson",
    "tech_stack",
    "concept",
    "general",
]


class Intent(BaseModel):
    # 검색 결과와 상관없이 질문 자체만 보고 판단한다. 자료가 없는 개발 질문도 true다
    is_dev_domain: bool = Field(description="질문이 소프트웨어 개발 도메인에 속하는지")
    kind: IntentKind = Field(description="질문의 의도 분류")
    is_follow_up: bool = Field(
        description="마지막 질문이 이전 대화의 주제를 이어받는 후속 질문인지"
    )
    search_query: str = Field(description="이전 대화 맥락을 풀어 쓴, 그 자체로 이해되는 검색 문장")
    key_terms: list[str] = Field(
        description="질문의 핵심 기술 용어·개념과 그 다른 표기(영문/한글/약어). 최대 8개"
    )
    needs_clarification: bool = Field(
        description="정보가 너무 부족해 검색이 무의미해서 사용자에게 되물어야 하는지"
    )
    clarifying_question: str | None = Field(
        description="needs_clarification이 true일 때 사용자에게 할 되묻는 질문. 아니면 null"
    )


class Candidate(BaseModel):
    card: KnowledgeCard
    # 벡터 유사도. 원문 청크와의 연결로만 찾은 카드는 None
    similarity: float | None
    # 이 카드의 근거 발췌문이 들어 있는 원문 후보 청크 id들
    linked_chunk_ids: list[str] = Field(default_factory=list)


class ChunkCandidate(BaseModel):
    # 원문 검색 후보 하나 = document_chunks 한 행 + 검색 점수 + 맥락용 앞뒤 청크
    id: str
    document_id: str
    document_title: str
    chunk_index: int
    location: str | None
    content: str
    # 같은 문서의 바로 앞/뒤 청크 본문. 답변 맥락을 넓히는 용도이고, 근거로 표시하는 것은
    # content뿐이다
    before: str | None = None
    after: str | None = None
    # 벡터·키워드 순위를 합친 RRF 점수
    score: float
    # 벡터 유사도. 키워드 검색으로만 들어온 청크는 None
    similarity: float | None = None


class Retrieval(BaseModel):
    # 검색 단계 결과. 카드와 원문 후보를 각각 순위 순으로 담는다
    cards: list[Candidate]
    chunks: list[ChunkCandidate]


class ChunkJudgement(BaseModel):
    index: int = Field(description="후보 원문 번호 (프롬프트에 붙은 번호 그대로)")
    relevant: bool = Field(description="이 원문이 질문에 답하는 데 필요한 정보를 담고 있는지")


class CardJudgement(BaseModel):
    index: int = Field(description="후보 카드 번호 (프롬프트에 붙은 번호 그대로)")
    relevant: bool = Field(description="이 카드가 질문에 답하는 데 실제로 도움이 되는지")
    reason: str = Field(description="판단 이유. 관련 있으면 이 카드를 고른 이유를 한 문장으로")


class VerificationOutput(BaseModel):
    # 항목별 판단을 먼저 하고, 그 판단을 바탕으로 전체(답할 수 있는지, 상충)를 정하도록
    # 이 순서로 둔다
    chunk_judgements: list[ChunkJudgement]
    card_judgements: list[CardJudgement]
    answerable: bool = Field(
        description="관련 있다고 판단한 원문·카드로 질문의 핵심에 답할 수 있는지 "
        "(일부만 답해도 true)"
    )
    conflicts: str | None = Field(
        description="관련 원문끼리 같은 대상에 대해 다른 사실·수치·결정을 말하면 그 내용. "
        "없으면 null"
    )


class VerifiedCandidate(BaseModel):
    candidate: Candidate
    # 사용자에게 보여줄 "이 카드를 고른 이유" (ChatReference.reason)
    reason: str


class Verification(BaseModel):
    # 검증 단계 결과. 관련 있다고 판단한 원문과 카드만 검색 순위 그대로 담는다
    chunks: list[ChunkCandidate]
    cards: list[VerifiedCandidate]
    # 자료로 질문에 답할 수 있는지. false면 "자료 없음" 안내로 답한다
    answerable: bool
    conflicts: str | None = None
