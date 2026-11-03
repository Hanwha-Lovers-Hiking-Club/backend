# 노드: 검색용 텍스트(search_text)를 KURE-v3(1024차원)로 임베딩한다.
# main의 embed_documents를 재사용한다. 저장용 임베딩이라 input_type은 "document"다.
import asyncio

from app.domains.baseline_documents.pipeline.state import EmbeddedChunk, IngestionState
from app.domains.documents.pipeline.embedding import embed_documents

# KURE API가 요청 1번에 받는 입력 개수 상한 (pipeline-tuning 브랜치에서 확인된 값)
BATCH_SIZE = 8


async def embed(state: IngestionState) -> dict:
    chunks = state["contextualized_chunks"]
    embedded: list[EmbeddedChunk] = []
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        try:
            # 임베딩 클라이언트가 동기라서 이벤트 루프를 막지 않도록 스레드에서 돌린다
            vectors = await asyncio.to_thread(
                embed_documents, [chunk["search_text"] for chunk in batch]
            )
        except Exception as e:
            return {"error": f"청크 임베딩 실패: {e}"}
        if len(vectors) != len(batch):
            return {"error": f"청크 임베딩 개수 불일치: 요청 {len(batch)}개, 응답 {len(vectors)}개"}
        embedded.extend({**chunk, "embedding": vector} for chunk, vector in zip(batch, vectors))
    return {"embedded_chunks": embedded}
