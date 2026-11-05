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


# 답변의 사실 근거로 쓴 원문 청크 하나 (document_chunks 한 행)
class ChatSource(CamelModel):
    # document_chunks.id
    chunk_id: UUID
    # documents.id
    document_id: UUID
    document_title: str
    location: str | None
    # 청크 본문 전체
    excerpt: str


class ChatReferencesEvent(CamelModel):
    # 관련 지식카드. 비어 있으면 프론트는 카드 영역을 그리지 않는다 (원문만으로 답한 경우 포함)
    items: list[ChatReference]
    # 답변 생성에 넘긴 원문 청크. 고정 문구로 답한 경우(범위 밖, 자료 없음, 되묻기)는 빈 목록
    sources: list[ChatSource] = Field(default_factory=list)


class ChatCompletedEvent(CamelModel):
    # 이 답변의 식별자. 피드백을 보낼 때 어떤 답변인지 가리킨다 (서버는 대화를 저장하지 않는다)
    message_id: UUID


class ChatErrorEvent(CamelModel):
    message: str
