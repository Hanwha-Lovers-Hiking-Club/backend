# 챗봇 내부에서만 쓰는 모델. LLM 구조화 출력(의도 파악/검증)과 검색 후보를 담는다.
# API 요청/응답 모양이 아니라서 schemas.py가 아니라 여기에 둔다.
from typing import Literal

from pydantic import BaseModel, Field

from app.domains.knowledge_cards.schemas import KnowledgeCard

# 명세서의 의도 예시(유사 프로젝트 / 비슷한 이슈 / 비슷한 교훈 / 비슷한 기술 스택 이슈)에
# "해결 방법"과 "범위 밖 질문"을 더했다
IntentKind = Literal[
    "similar_project",
    "similar_issue",
    "solution",
    "similar_lesson",
    "tech_stack",
    "general",
    "off_topic",
]


class Intent(BaseModel):
    kind: IntentKind = Field(description="질문의 의도 분류")
    search_query: str = Field(
        description="이전 대화 맥락을 풀어 쓴, 그 자체로 이해되는 지식카드 검색 문장"
    )
    needs_clarification: bool = Field(
        description="정보가 너무 부족해 검색이 무의미해서 사용자에게 되물어야 하는지"
    )
    clarifying_question: str | None = Field(
        description="needs_clarification이 true일 때 사용자에게 할 되묻는 질문. 아니면 null"
    )


class Candidate(BaseModel):
    card: KnowledgeCard
    similarity: float


class CardJudgement(BaseModel):
    index: int = Field(description="후보 카드 번호 (프롬프트에 붙은 번호 그대로)")
    relevant: bool = Field(description="이 카드가 질문에 답하는 데 실제로 도움이 되는지")
    reason: str = Field(description="판단 이유. 관련 있으면 이 카드를 고른 이유를 한 문장으로")


class VerificationOutput(BaseModel):
    judgements: list[CardJudgement]


class VerifiedCandidate(BaseModel):
    candidate: Candidate
    # 사용자에게 보여줄 "이 카드를 고른 이유" (ChatReference.reason)
    reason: str
