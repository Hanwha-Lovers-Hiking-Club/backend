# 노드: 청크와 임베딩을 baseline_chunks 테이블에 저장한다.
# 문서마다 기존 청크를 지우고 새로 넣으므로, 같은 문서를 여러 번 인덱싱해도 청크가 중복되지 않는다.
import asyncio

from app.domains.baseline_documents import repository
from app.domains.baseline_documents.pipeline.state import EmbeddedChunk, IngestionState


def _to_row(chunk: EmbeddedChunk) -> dict:
    return {
        "document_id": str(chunk["document_id"]),
        "chunk_index": chunk["chunk_index"],
        # 헤딩이 없는 문서의 빈 location은 DB에 NULL로 저장한다
        "location": chunk["location"] or None,
        "content": chunk["content"],
        "search_text": chunk["search_text"],
        "embedding": chunk["embedding"],
    }


async def save(state: IngestionState) -> dict:
    rows_by_document: dict[str, list[dict]] = {
        str(document["id"]): [] for document in state["documents"]
    }
    for chunk in state["embedded_chunks"]:
        rows_by_document[str(chunk["document_id"])].append(_to_row(chunk))

    for document_id, rows in rows_by_document.items():
        try:
            # supabase 클라이언트가 동기라서 이벤트 루프를 막지 않도록 스레드에서 돌린다
            await asyncio.to_thread(repository.replace_chunks, document_id, rows)
        except Exception as e:
            return {"error": f"문서 {document_id}의 청크 저장 실패: {e}"}

    return {"chunk_counts": {doc_id: len(rows) for doc_id, rows in rows_by_document.items()}}
