# 기본 RAG 챗봇 API의 요청/응답 모양(pydantic 모델)만 정의한다.
# 요청(ChatRequest)과 token/completed/error 이벤트는 기존 챗봇(chat/schemas.py)과 같은 모양을 쓴다.
# references만 다르다. 기존 챗봇은 지식카드를 근거로 보내고, 기본 RAG는 문서 청크를 근거로 보낸다.
from uuid import UUID

from app.core.schemas import CamelModel


class BaselineReference(CamelModel):
    # 답변 속 인용 번호. [1]이면 1
    number: int
    # baseline_chunks.id
    chunk_id: UUID
    document_id: UUID
    document_title: str
    location: str | None
    # 청크 본문 (앞뒤 청크를 붙이기 전)
    excerpt: str
    # 재순위 점수. RERANK_API_KEY가 없으면 RRF 점수
    score: float


class BaselineReferencesEvent(CamelModel):
    items: list[BaselineReference]
