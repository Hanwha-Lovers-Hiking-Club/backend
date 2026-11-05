# 노드: 업로드된 파일(pdf/docx/md 등)에서 텍스트를 추출한다.

import asyncio

from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.parsers import parse_document
from app.domains.documents.pipeline.parsers.errors import ParseError
from app.domains.documents.pipeline.processing import ProcessingFailure
from app.domains.documents.pipeline.state import IngestionState
from app.domains.documents.pipeline.verification.reset import reset_verification


async def parse(state: IngestionState) -> dict:
    # Run synchronous file/SDK work outside the event loop. No Storage/DB writes.
    def read_sources():
        parsed, failures = [], list(state.get("processing_failures", []))
        for source in state["sources"]:
            try:
                parsed.append(parse_document(source, state.get("parse_options")))
                failures = [
                    item
                    for item in failures
                    if not (item.stage == "parse" and item.document_id == source.document_id)
                ]
            except ParseError as exc:
                if "document_progress" not in state:
                    raise
                failures.append(
                    ProcessingFailure(
                        document_id=source.document_id,
                        stage="parse",
                        code=exc.code,
                    )
                )
        return parsed, failures

    parsed, failures = await asyncio.to_thread(read_sources)

    return {
        **reset_verification(),
        "parsed_documents": parsed,
        "processing_failures": failures,
        "documents": [],
        "chunks": [],
        "extraction_result": ExtractionResult(),
        "drafts": [],
        "draft_skips": [],
    }
