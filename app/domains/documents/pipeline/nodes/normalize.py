"""Node: convert parser output into stable DocumentIR blocks and evidence IDs."""

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
