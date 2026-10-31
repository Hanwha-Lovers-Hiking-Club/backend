# IngestionState 정의. 노드끼리 주고받는 유일한 데이터 계약이다.
# 필드를 바꾸면 상대 담당자에게 알리고 PR에서 확인받는다.
#
# 그래프는 job(업로드 요청) 1개당 1번 invoke한다. 문서 1건당 1번이 아닌 이유:
# 문서 여러 개가 합쳐져 카드 1개가 되거나, 문서 1건에서 카드가 여러 개 나올 수 있어서(M:N)
# extract/draft_card가 job에 속한 모든 문서의 청크를 한꺼번에 봐야 한다.
from typing import TypedDict
from uuid import UUID

from app.domains.documents.pipeline.models import (
    CardDraft,
    EmbeddedCard,
    EmbeddedChunk,
    ExtractedElements,
    VerifiedCard,
)


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


# chunk 노드의 출력 타입. state.IngestionState.chunks에 쌓인다
class Chunk(TypedDict):
    id: str  # f"{document_id}:{index}" 형태. extract 결과가 어떤 청크에서 왔는지 역추적용
    document_id: UUID
    index: int  # 문서 안에서의 순서(0부터). document_chunks.chunk_index로 저장된다
    text: str
    # 근거 표시용 원문 내 위치. 예: "2장 · 답변 근거와 지식 연결"
    # ponytail: parse 노드가 DoclingDocument(페이지/좌표 정보 있음)를 마크다운 문자열로 평평하게
    # 만들어버려서, 섹션(헤딩) 단위까지만 가능하고 페이지 번호는 못 뽑는다. 페이지 번호가 필요해지면
    # parse 노드가 DoclingDocument를 버리지 않고 state로 들고 다니게 바꿔야 한다.
    location: str


# 그래프 전체가 주고받는 state. graph.py가 job 1개당 1번 이 타입으로 invoke한다
class IngestionState(TypedDict):
    job_id: UUID
    documents: list[DocumentInput]
    parsed_documents: list[ParsedDocument]
    chunks: list[Chunk]
    extracted_elements: list[ExtractedElements]
    card_drafts: list[CardDraft]
    verified_cards: list[VerifiedCard]
    # verified_cards 중 verdict == "pass"만 꺼내 임베딩까지 붙인 카드. save 노드가 이것만 저장한다
    embedded_cards: list[EmbeddedCard]
    # save 노드가 실제로 저장한 카드 수. 일부 카드가 저장에 실패해도 나머지는 저장되므로
    # len(embedded_cards)와 다를 수 있다. SSE completed의 created_card_count는 이 값을 쓴다
    saved_card_count: int
    # embed_chunks 노드의 출력. save_chunks 노드가 document_chunks에 저장한다.
    # 카드 경로와 독립이라, 임베딩에 실패하면 비어 있고 error가 아니라 warnings에 사유가 남는다
    embedded_chunks: list[EmbeddedChunk]
    # 카드 생성은 성공했지만 보조 기능(청크 검색 인덱싱)이 실패했을 때의 사유.
    # job은 completed로 끝난다
    warnings: list[str]
    # documents/service.py의 STEP_PROGRESS 인덱스에 대응하는 그래프 진행 단계
    active_step: int
    error: str | None
