# 노드: 모든 청크를 kure-v3로 임베딩한다.
# 챗봇이 카드에 없는 원문 내용을 검색할 때 쓰는 보조 인덱스다.
#
# 카드 경로와 독립이다: 카드가 0개여도 청크는 임베딩하고, 임베딩이 실패해도 job을 실패시키지 않는다.
# kure 호출의 일시적 오류 재시도는 embedding.py의 클라이언트(SDK)가 맡고, 그래도 실패하면
# state["error"]가 아니라 state["warnings"]에 사유를 남기고 빈 목록으로 끝낸다.
# 임베딩 텍스트는 location(섹션 경로)을 앞에 붙인다 — 헤딩 정보가 벡터에 반영되어 검색이 좋아진다.
# 저장되는 content는 항상 원문 그대로다.
import logging

from app.core.logging import summarize_error
from app.domains.documents.pipeline.embedding import embed_documents
from app.domains.documents.pipeline.models import EmbeddedChunk
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

# ponytail: kure의 입력 개수 상한을 몰라서 보수적으로 잡은 임시값. 실제 한도를 확인하면 조정
BATCH_SIZE = 32


def _embedding_text(chunk: Chunk) -> str:
    return f"{chunk['location']}\n\n{chunk['text']}" if chunk["location"] else chunk["text"]


def embed_chunks(state: IngestionState) -> dict:
    chunks = state["chunks"]
    embedded: list[EmbeddedChunk] = []
    try:
        for start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[start : start + BATCH_SIZE]
            vectors = embed_documents([_embedding_text(c) for c in batch])
            embedded.extend(
                EmbeddedChunk(
                    document_id=c["document_id"],
                    chunk_index=c["index"],
                    location=c["location"] or None,
                    content=c["text"],
                    embedding=vector,
                )
                for c, vector in zip(batch, vectors)
            )
    except Exception as e:
        # 일부만 성공한 상태로 저장하면 문서가 반쪽만 검색되므로 전부 버린다
        logger.warning(
            "embed_chunks: 청크 임베딩 실패, 청크 인덱싱을 건너뜀: %s", summarize_error(e)
        )
        return {
            "embedded_chunks": [],
            "warnings": [*state.get("warnings", []), f"청크 임베딩 실패: {summarize_error(e)}"],
        }

    logger.info("embed_chunks: 청크 %d개 임베딩 완료", len(embedded))
    return {"embedded_chunks": embedded}
