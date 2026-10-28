# 노드: 추출한 텍스트를 청크로 나눈다. 근거 표시용 위치(location) 정보를 같이 유지한다.

from app.domains.documents.pipeline.chunking import chunk_document
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.state import IngestionState


def chunk(state: IngestionState) -> dict:
    return {
        "chunks": [
            item
            for document in state["documents"]
            for item in chunk_document(document, state.get("chunk_options"))
        ],
        "extraction_result": ExtractionResult(),
        "drafts": [],
        "draft_skips": [],
    }
