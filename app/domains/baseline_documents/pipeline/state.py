# 기본 RAG 인덱싱 파이프라인의 노드들이 주고받는 상태를 정의한다.
# 그래프 전체 상태(IngestionState)는 parse/embed/save 노드를 만들 때 추가한다.
from typing import TypedDict
from uuid import UUID


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
