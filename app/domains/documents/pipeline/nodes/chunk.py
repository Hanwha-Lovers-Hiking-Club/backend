# 노드: 추출한 텍스트를 청크로 나눈다. 근거 표시용 위치(location) 정보를 같이 유지한다.

from app.domains.documents.pipeline.chunking import chunk_document
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.state import IngestionState
from app.domains.documents.pipeline.verification.reset import reset_verification


def chunk(state: IngestionState) -> dict:
    document_ids = {item.document_id for item in state["documents"]}
    return {
        **reset_verification(),
        # 상위 단계에서 다시 읽거나 분할하면 해당 문서의 오래된 추출 결과를 무효화한다.
        "chunk_progress": {
            key: item
            for key, item in state.get("chunk_progress", {}).items()
            if item.document_id not in document_ids
        },
        "processing_failures": [
            item
            for item in state.get("processing_failures", [])
            if not (item.stage == "extract" and item.document_id in document_ids)
        ],
        "chunks": [
            item
            for document in state["documents"]
            for item in chunk_document(document, state.get("chunk_options"))
        ],
        "extraction_result": ExtractionResult(),
        "drafts": [],
        "draft_skips": [],
    }
