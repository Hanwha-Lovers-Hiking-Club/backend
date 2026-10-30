# 노드: 파싱 결과를 DocumentIR로 변환하고 재현 가능한 블록·근거 식별자를 부여한다.

from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.normalization import normalize_document
from app.domains.documents.pipeline.state import IngestionState
from app.domains.documents.pipeline.verification.reset import reset_verification


def normalize(state: IngestionState) -> dict:
    return {
        **reset_verification(),
        "documents": [normalize_document(parsed) for parsed in state["parsed_documents"]],
        "chunks": [],
        "extraction_result": ExtractionResult(),
        "drafts": [],
        "draft_skips": [],
    }
