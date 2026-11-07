# 프로젝트 API의 요청/응답 모양(pydantic 모델)만 정의한다.
from datetime import datetime
from uuid import UUID

from app.core.schemas import CamelModel
from app.domains.knowledge_cards.schemas import KnowledgeCard


class ProjectSummary(CamelModel):
    id: UUID
    name: str
    document_count: int
    # 상세의 cards와 같은 기준(active)으로 센다
    card_count: int
    created_at: datetime


class ProjectListResponse(CamelModel):
    items: list[ProjectSummary]


class ProjectDocument(CamelModel):
    id: UUID
    title: str
    original_filename: str
    size_bytes: int
    created_at: datetime


class ProjectDetail(CamelModel):
    id: UUID
    name: str
    created_at: datetime
    documents: list[ProjectDocument]
    cards: list[KnowledgeCard]
