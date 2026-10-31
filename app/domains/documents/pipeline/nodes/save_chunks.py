# 노드: embed_chunks가 만든 청크를 document_chunks에 저장한다.
#
# document_chunks는 documents 도메인의 테이블이라 knowledge_cards service를 거치지 않고
# 이 도메인의 repository를 직접 쓴다. 카드 경로와 독립이라 실패해도 job은 실패하지 않는다:
# 이미 들어간 이 job 문서들의 청크를 지우고(반쪽 인덱스를 남기지 않음) state["warnings"]에만 남긴다.
import logging

from app.core.logging import summarize_error
from app.domains.documents import repository
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)

# 벡터가 1024차원이라 행이 커서 한 번에 넣으면 요청이 너무 커진다
INSERT_BATCH_SIZE = 100


def save_chunks(state: IngestionState) -> dict:
    embedded = state["embedded_chunks"]
    if not embedded:
        return {}

    rows = [
        {
            "document_id": str(c.document_id),
            "chunk_index": c.chunk_index,
            "location": c.location,
            "content": c.content,
            "embedding": c.embedding,
        }
        for c in embedded
    ]
    try:
        for start in range(0, len(rows), INSERT_BATCH_SIZE):
            repository.insert_chunks(rows[start : start + INSERT_BATCH_SIZE])
    except Exception as e:
        logger.warning(
            "save_chunks: 청크 저장 실패, 이미 넣은 청크를 정리함: %s", summarize_error(e)
        )
        try:
            repository.delete_chunks(sorted({r["document_id"] for r in rows}))
        except Exception:
            logger.exception("save_chunks: 실패한 청크 정리도 실패")
        return {"warnings": [*state.get("warnings", []), f"청크 저장 실패: {summarize_error(e)}"]}

    logger.info("save_chunks: 청크 %d개 저장 완료", len(rows))
    return {}
