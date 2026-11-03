# 노드: 선택한 청크와 같은 문서의 앞뒤 청크(chunk_index ± 1)를 가져와 답변 컨텍스트를 넓힌다.
# 근거로 표시하는 것은 원래 선택한 청크뿐이다.
#
# 청크는 50토큰씩 겹치게 잘랐기 때문에, 이어 붙인 컨텍스트에는 경계 부분 문장이 두 번 나올 수 있다.
# 답변 생성에는 문제가 없어서 겹침을 제거하지 않는다.
import asyncio

from app.domains.baseline_chat import repository
from app.domains.baseline_chat.pipeline.state import ContextChunk, RetrievalState


async def expand(state: RetrievalState) -> dict:
    reranked = state["reranked"]
    if not reranked:
        return {"contexts": []}

    # 문서마다 DB를 한 번만 조회하도록 필요한 앞뒤 순번을 문서별로 모은다
    wanted: dict[str, set[int]] = {}
    for chunk in reranked:
        indexes = wanted.setdefault(chunk["document_id"], set())
        indexes.update({chunk["chunk_index"] - 1, chunk["chunk_index"] + 1})

    document_ids = list(wanted)
    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                repository.get_chunks_by_index,
                document_id,
                sorted(i for i in wanted[document_id] if i >= 0),
            )
            for document_id in document_ids
        )
    )
    neighbors: dict[tuple[str, int], str] = {
        (document_id, row["chunk_index"]): row["content"]
        for document_id, rows in zip(document_ids, results)
        for row in rows
    }

    contexts: list[ContextChunk] = []
    for chunk in reranked:
        key_before = (chunk["document_id"], chunk["chunk_index"] - 1)
        key_after = (chunk["document_id"], chunk["chunk_index"] + 1)
        parts = [neighbors.get(key_before), chunk["content"], neighbors.get(key_after)]
        contexts.append({**chunk, "context": "\n".join(part for part in parts if part)})
    return {"contexts": contexts}
