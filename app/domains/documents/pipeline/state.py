# IngestionState 정의. 노드끼리 주고받는 유일한 데이터 계약이다.
# 필드를 바꾸면 상대 담당자에게 알리고 PR에서 확인받는다.

from typing import TypedDict

from app.domains.documents.pipeline.document import (
    Chunk,
    ChunkOptions,
    DocumentIR,
    DocumentSource,
    ParsedDocument,
    ParseOptions,
)
from app.domains.documents.pipeline.models import DraftSkip, ExtractionResult, KnowledgeCardDraft


class IngestionState(TypedDict, total=False):
    """Upstream retries clear stale extraction. Draft must retain the current extraction."""

    sources: list[DocumentSource]
    parse_options: ParseOptions
    chunk_options: ChunkOptions
    parsed_documents: list[ParsedDocument]
    documents: list[DocumentIR]
    chunks: list[Chunk]
    extraction_result: ExtractionResult
    drafts: list[KnowledgeCardDraft]
    draft_skips: list[DraftSkip]
