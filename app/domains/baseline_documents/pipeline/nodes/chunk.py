# 노드: 헤딩(섹션) 경계를 지키며 텍스트를 500토큰 이하로 자른다 (overlap 50토큰).
# 섹션 분할은 main의 documents 파이프라인 함수를 그대로 가져다 쓴다.
#
# 섹션 단위로 먼저 나눈 뒤 섹션 안에서만 자른다. 그래서 청크가 두 섹션에 걸치지 않고,
# location이 항상 정확하다.
# 주의: _split_sections는 main 쪽 private 함수다. main을 merge할 때 이름이나 반환 모양이
# 바뀌었는지 확인한다.
import semchunk

from app.domains.baseline_documents.pipeline.state import Chunk, ParsedDocument
from app.domains.documents.pipeline.nodes.chunk import _split_sections, _tokenizer

CHUNK_SIZE_TOKENS = 500
# 문장이 청크 경계에서 잘려도 앞뒤 청크 어느 쪽에서든 검색되도록 겹치게 자른다
CHUNK_OVERLAP_TOKENS = 50

_chunker = semchunk.chunkerify(_tokenizer, chunk_size=CHUNK_SIZE_TOKENS)


def split_document(document: ParsedDocument) -> list[Chunk]:
    chunks: list[Chunk] = []
    for location, section_text in _split_sections(document["text"]):
        for piece in _chunker(section_text, overlap=CHUNK_OVERLAP_TOKENS):
            if not piece.strip():
                continue
            chunks.append(
                {
                    "document_id": document["document_id"],
                    "chunk_index": len(chunks),
                    "location": location,
                    "content": piece,
                }
            )
    return chunks


def chunk(state: dict) -> dict:
    chunks: list[Chunk] = []
    for document in state["parsed_documents"]:
        chunks.extend(split_document(document))
    return {"chunks": chunks}
