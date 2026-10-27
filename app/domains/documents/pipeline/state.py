# IngestionState 정의. 노드끼리 주고받는 유일한 데이터 계약이다.
# 필드를 바꾸면 상대 담당자에게 알리고 PR에서 확인받는다.
#
# 그래프는 job(업로드 요청) 1개당 1번 invoke한다. 문서 1건당 1번이 아닌 이유:
# 문서 여러 개가 합쳐져 카드 1개가 되거나, 문서 1건에서 카드가 여러 개 나올 수 있어서(M:N)
# extract/draft_card가 job에 속한 모든 문서의 청크를 한꺼번에 봐야 한다.
from typing import TypedDict
from uuid import UUID

from app.domains.documents.pipeline.models import CardDraft, ExtractedElements, VerifiedCard


# state.IngestionState.documents의 원소 타입. service.py가 job 생성 시 채워서 넘긴다
class DocumentInput(TypedDict):
    id: UUID
    storage_bucket: str
    storage_path: str
    original_filename: str


# parse 노드의 출력 타입. state.IngestionState.parsed_documents에 쌓인다
class ParsedDocument(TypedDict):
    document_id: UUID
    text: str


# chunk 노드의 출력 타입. state.IngestionState.chunks/embeddings가 이 순서를 그대로 따른다
class Chunk(TypedDict):
    id: str  # f"{document_id}:{index}" 형태. extract 결과가 어떤 청크에서 왔는지 역추적용
    document_id: UUID
    text: str
    location: str  # 근거 표시용 원문 내 위치. 예: "2장 · 답변 근거와 지식 연결"


# 그래프 전체가 주고받는 state. graph.py가 job 1개당 1번 이 타입으로 invoke한다
class IngestionState(TypedDict):
    job_id: UUID
    documents: list[DocumentInput]
    parsed_documents: list[ParsedDocument]
    chunks: list[Chunk]
    # chunks와 같은 순서로 1:1 대응하는 임베딩 벡터
    embeddings: list[list[float]]
    extracted_elements: list[ExtractedElements]
    card_drafts: list[CardDraft]
    verified_cards: list[VerifiedCard]
    # documents/service.py의 STEP_PROGRESS 인덱스에 대응하는 그래프 진행 단계
    active_step: int
    error: str | None
