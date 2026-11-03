# 기본 RAG 문서 도메인의 진입점 로직.
# 이미 업로드된 문서를 id로 받아 파이프라인을 실행한다.
# 순서: parse -> chunk -> contextualize -> embed -> save
#
# 실험용 API라서 job/SSE 없이 동기로 처리한다. 청크마다 LLM을 호출하므로 문서가 많으면
# 응답이 수십 초 이상 걸릴 수 있다. save가 문서 단위로 저장하므로, 중간에 실패하면 앞 문서들은
# 이미 저장된 상태로 남는다. 다시 요청하면 덮어쓴다.
import asyncio
from functools import lru_cache
from uuid import UUID

from fastapi import HTTPException
from langgraph.graph.state import CompiledStateGraph
from postgrest.exceptions import APIError

from app.domains.baseline_documents import repository
from app.domains.baseline_documents.schemas import IndexedDocument, IndexResponse

LOOKUP_FAILED_DETAIL = "문서 정보를 조회하지 못했습니다."


@lru_cache
def get_graph() -> CompiledStateGraph:
    # parse 노드가 import 시점에 docling 변환기를 만든다. 서버 시작과 테스트가 느려지지 않도록
    # 그래프는 처음 인덱싱을 요청할 때 import한다
    from app.domains.baseline_documents.pipeline.graph import build_graph

    return build_graph()


async def index_documents(document_ids: list[UUID]) -> IndexResponse:
    # 중복 ID는 순서를 지키며 한 번만 남긴다
    ids = [str(document_id) for document_id in dict.fromkeys(document_ids)]

    try:
        rows = await asyncio.to_thread(repository.get_documents, ids)
    except APIError:
        raise HTTPException(status_code=500, detail=LOOKUP_FAILED_DETAIL)

    # 하나라도 없으면 아무것도 처리하지 않는다 (전부 검증한 뒤 처리)
    found = {row["id"] for row in rows}
    missing = [document_id for document_id in ids if document_id not in found]
    if missing:
        detail = f"문서를 찾을 수 없습니다: {', '.join(missing)}"
        raise HTTPException(status_code=404, detail=detail)

    rows_by_id = {row["id"]: row for row in rows}
    documents = [
        {
            "id": UUID(document_id),
            "title": rows_by_id[document_id]["title"],
            "storage_path": rows_by_id[document_id]["storage_path"],
            "original_filename": rows_by_id[document_id]["original_filename"],
        }
        for document_id in ids
    ]

    result = await get_graph().ainvoke({"documents": documents})
    if result.get("error"):
        # 실험용 API라서 원인을 바로 볼 수 있도록 노드가 남긴 메시지를 그대로 돌려준다
        raise HTTPException(status_code=500, detail=f"인덱싱 실패: {result['error']}")

    chunk_counts = result["chunk_counts"]
    return IndexResponse(
        items=[
            IndexedDocument(document_id=UUID(document_id), chunk_count=chunk_counts[document_id])
            for document_id in ids
        ]
    )
