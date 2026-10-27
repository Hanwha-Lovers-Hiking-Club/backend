# 챗봇 API의 요청/응답 모양(pydantic 모델)만 정의한다.
# JSON 키는 API 명세서(API-001)대로 camelCase로 주고받는다 (CamelModel).
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import CamelModel
from app.domains.knowledge_cards.schemas import KnowledgeCard


class ChatMessage(CamelModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(CamelModel):
    message: str = Field(min_length=1, description="사용자 질문")
    # 대화 내역은 프론트(Zustand)가 관리하고, 서버는 저장하지 않고 매 요청마다 받는다
    history: list[ChatMessage] = Field(default_factory=list, description="이전 대화 내역")


# 챗봇 응답 SSE(API-001)의 이벤트별 data 모양
class ChatTokenEvent(CamelModel):
    content: str


# 참조 하나 = 카드 하나 + 그 카드의 근거 하나. 모양은 프론트 ChatReference 타입을 따른다
# 설계서 예시의 cardId/documentId 대신, 프론트가 다시 조회하지 않도록 카드 전체를 싣는다
class ChatSourceDocument(CamelModel):
    # documents.id
    id: UUID
    document_title: str
    location: str | None
    excerpt: str | None


class ChatReference(CamelModel):
    # card_documents.id (카드의 근거 행). 한 답변의 참조 목록 안에서 겹치지 않는다
    id: UUID
    type: Literal["knowledge-card"]
    # 카드 제목. 답변 아래 참조 버튼에 표시된다
    label: str
    card: KnowledgeCard
    # 이 카드를 고른 이유. 답변 생성 시 만들어지는 값이라 DB에 저장하지 않는다
    reason: str
    document: ChatSourceDocument


class ChatReferencesEvent(CamelModel):
    items: list[ChatReference]


class ChatCompletedEvent(CamelModel):
    pass


class ChatErrorEvent(CamelModel):
    message: str
