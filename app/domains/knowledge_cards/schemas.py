# 지식카드 요청/응답 모양. 필드는 docs/schema.md의 knowledge_cards 컬럼을 그대로 따르고,
# JSON 키는 API 명세서(API-004)대로 camelCase로 나간다 (CamelModel).
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.core.schemas import CamelModel

# recent: updated_at DESC / created: created_at DESC / name: title ASC
CardSort = Literal["recent", "created", "name"]
CardStatus = Literal["draft", "active", "flagged", "archived"]


class CardEvidence(CamelModel):
    # card_documents.id. 한 카드가 같은 문서를 여러 번 근거로 삼을 수 있어서
    # 명세서의 "카드ID:문서ID" 복합키 대신 행마다 유일한 PK를 쓴다
    id: UUID
    document_id: UUID
    document_title: str
    original_filename: str
    location: str | None
    excerpt: str | None
    # 원본 문서 열람 URL은 별도 API(/api/documents/{id}/url)로 발급 예정이라 지금은 항상 null
    url: str | None = None


class KnowledgeCard(CamelModel):
    id: UUID
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
    status: CardStatus
    quality_score: float | None
    last_verified_at: datetime | None
    verification_version: str | None
    project_name: str | None
    department: str | None
    project_date: date | None
    created_at: datetime
    updated_at: datetime
    evidences: list[CardEvidence]


class KnowledgeCardListResponse(CamelModel):
    items: list[KnowledgeCard]
    # 페이지네이션을 적용하기 전, 검색 조건에 맞는 전체 카드 수
    total: int
