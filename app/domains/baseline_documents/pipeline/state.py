# 기본 RAG 인덱싱 파이프라인의 노드들이 주고받는 상태를 정의한다.
from typing import TypedDict
from uuid import UUID


# service.py가 documents 테이블에서 읽어 그래프에 넘기는 원본 문서 정보
class DocumentInput(TypedDict):
    id: UUID
    title: str
    storage_path: str
    original_filename: str


# parse 노드의 출력. chunk와 contextualize가 읽는다
class ParsedDocument(TypedDict):
    document_id: UUID
    # documents.title. contextualize가 검색용 텍스트의 헤더로 쓴다
    title: str
    text: str


# chunk 노드의 출력. baseline_chunks 테이블의 한 행이 된다
class Chunk(TypedDict):
    document_id: UUID
    # 문서 안 순서 (0부터). 챗봇의 앞뒤 청크 붙이기에 쓴다
    chunk_index: int
    # 섹션 경로. 예: "2장 · 답변 근거와 지식 연결". 헤딩이 없는 문서는 빈 문자열
    location: str
    # 청크 본문. 답변 컨텍스트와 근거 표시에 쓴다
    content: str


# contextualize 노드의 출력
class ContextualizedChunk(Chunk):
    # 헤더 + 문맥 설명 + 본문. 임베딩과 BM25 검색의 대상이다
    search_text: str


# embed 노드의 출력. save 노드가 이것을 저장한다
class EmbeddedChunk(ContextualizedChunk):
    # search_text의 kure-v3 1024차원 임베딩
    embedding: list[float]


# 그래프 전체가 주고받는 상태. service.py가 인덱싱 요청 1번에 그래프를 1번 실행한다
class IngestionState(TypedDict, total=False):
    documents: list[DocumentInput]
    parsed_documents: list[ParsedDocument]
    chunks: list[Chunk]
    contextualized_chunks: list[ContextualizedChunk]
    embedded_chunks: list[EmbeddedChunk]
    # save 노드가 채운다. 문서 id(str) -> 저장한 청크 수. 청크가 0개인 문서도 0으로 들어간다
    chunk_counts: dict[str, int]
    # 어느 노드든 이 값을 채우면 남은 노드를 건너뛰고 끝난다
    error: str | None
