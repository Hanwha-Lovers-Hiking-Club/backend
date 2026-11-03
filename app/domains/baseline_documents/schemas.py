# 기본 RAG 문서 인덱싱 API의 요청/응답 모양(pydantic 모델)만 정의한다.
# 다른 도메인과 같게 JSON 키는 camelCase로 주고받는다 (CamelModel).
from uuid import UUID

from pydantic import Field

from app.core.schemas import CamelModel


class IndexRequest(CamelModel):
    document_ids: list[UUID] = Field(
        min_length=1, description="인덱싱할 문서 ID (documents 테이블에 이미 있는 문서)"
    )


class IndexedDocument(CamelModel):
    document_id: UUID
    # baseline_chunks에 저장한 청크 수. 텍스트가 없는 문서는 0
    chunk_count: int


class IndexResponse(CamelModel):
    # 요청한 순서대로 (중복 ID는 한 번만)
    items: list[IndexedDocument]
