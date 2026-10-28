# 노드: 업로드된 파일(pdf/docx/md 등)에서 텍스트를 추출한다.

import asyncio

from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.parsers import parse_document
from app.domains.documents.pipeline.state import IngestionState


async def parse(state: IngestionState) -> dict:
    # Run synchronous file/SDK work outside the event loop. No Storage/DB writes.
    def read_sources():
        return [parse_document(source, state.get("parse_options")) for source in state["sources"]]

    return {
        "parsed_documents": await asyncio.to_thread(read_sources),
        "documents": [],
        "chunks": [],
        "extraction_result": ExtractionResult(),
        "drafts": [],
        "draft_skips": [],
    }
