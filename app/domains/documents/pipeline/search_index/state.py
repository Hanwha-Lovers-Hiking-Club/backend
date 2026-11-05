# 원문 검색 인덱싱 경로가 주고받는 데이터 계약. dev의 IngestionState 중 인덱싱에 쓰던 필드만 옮겼다.
# 필드 의미는 dev와 같다. 카드 생성 State(pipeline/state.py)와 섞지 않는다.
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel


# service.py가 작업 시작 시 채워서 넘긴다
class DocumentInput(TypedDict):
    id: UUID
    storage_bucket: str
    storage_path: str
    original_filename: str


# parse의 출력. 검색용 청크를 만들 마크다운/원문 텍스트
class ParsedDocument(TypedDict):
    document_id: UUID
    text: str


# chunk의 출력
class Chunk(TypedDict):
    id: str  # f"{document_id}:{index}" 형태
    document_id: UUID
    index: int  # 문서 안에서의 순서(0부터). document_chunks.chunk_index로 저장된다
    text: str
    # 근거 표시용 원문 내 위치(헤딩 경로). 예: "2장 · 답변 근거와 지식 연결"
    location: str


# embed_chunks의 출력(save_chunks가 저장)
# content는 원문 그대로이고, 임베딩은 location을 앞에 붙인 텍스트로 만든 것이다
class EmbeddedChunk(BaseModel):
    document_id: UUID
    chunk_index: int
    location: str | None
    content: str
    embedding: list[float]


class IndexState(TypedDict, total=False):
    job_id: UUID
    documents: list[DocumentInput]
    parsed_documents: list[ParsedDocument]
    # 읽지 못한 문서와 사유
    parse_failures: list[str]
    chunks: list[Chunk]
    embedded_chunks: list[EmbeddedChunk]
    # 인덱싱 실패 사유. 작업은 실패시키지 않는다
    warnings: list[str]
    # save_chunks가 청크를 저장한 문서 수. 원문 검색이 가능한 문서 수다
    indexed_document_count: int
    # 모든 문서를 읽지 못했을 때만 설정된다
    error: str | None
