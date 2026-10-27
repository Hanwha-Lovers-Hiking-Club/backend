# 챗봇 API의 요청/응답 모양(pydantic 모델)만 정의한다.
# JSON 키는 API 명세서(API-001)대로 camelCase로 주고받는다 (CamelModel).
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import CamelModel


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


class ChatReference(CamelModel):
    card_id: UUID
    reason: str
    document_id: UUID
    location: str | None
    excerpt: str | None


class ChatReferencesEvent(CamelModel):
    items: list[ChatReference]


class ChatCompletedEvent(CamelModel):
    pass


class ChatErrorEvent(CamelModel):
    message: str
