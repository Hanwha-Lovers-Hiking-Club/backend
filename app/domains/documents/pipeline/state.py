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
from app.domains.documents.pipeline.embedding_texts import CardEmbeddingTexts, EmbeddingTextOptions
from app.domains.documents.pipeline.embeddings.models import CardEmbeddings, EmbeddingUsage
from app.domains.documents.pipeline.models import DraftSkip, ExtractionResult, KnowledgeCardDraft
from app.domains.documents.pipeline.processing import (
    ChunkProgress,
    DocumentProgress,
    ProcessingFailure,
    ProcessingOptions,
    SaveReceipt,
)
from app.domains.documents.pipeline.verification.models import (
    RetryRequest,
    RetryWork,
    VerificationResult,
)


class IngestionInput(TypedDict, total=False):
    sources: list[DocumentSource]
    parse_options: ParseOptions
    chunk_options: ChunkOptions
    embedding_text_options: EmbeddingTextOptions
    processing_options: ProcessingOptions


class IngestionState(TypedDict, total=False):
    """Upstream retries clear stale extraction. Draft must retain the current extraction."""

    sources: list[DocumentSource]
    processing_options: ProcessingOptions
    chunk_progress: dict[str, ChunkProgress]
    active_chunk_keys: list[str]
    document_progress: dict[str, DocumentProgress]
    active_document_id: str | None
    document_save_receipt: SaveReceipt | None
    processing_failures: list[ProcessingFailure]
    parse_options: ParseOptions
    chunk_options: ChunkOptions
    embedding_text_options: EmbeddingTextOptions
    embedding_texts: list[CardEmbeddingTexts]
    embeddings: list[CardEmbeddings]
    embedding_usage: list[EmbeddingUsage]
    parsed_documents: list[ParsedDocument]
    documents: list[DocumentIR]
    chunks: list[Chunk]
    extraction_result: ExtractionResult
    drafts: list[KnowledgeCardDraft]
    draft_skips: list[DraftSkip]
    verification_results: list[VerificationResult]
    verified_drafts: list[KnowledgeCardDraft]
    review_drafts: list[KnowledgeCardDraft]
    retry_requests: list[RetryRequest]
    retry_feedback: list[RetryRequest]
    verification_history: list[RetryRequest]
    all_sources: list[DocumentSource]
    all_parsed_documents: list[ParsedDocument]
    all_documents: list[DocumentIR]
    all_chunks: list[Chunk]
    settled_results: list[VerificationResult]
    settled_drafts: list[KnowledgeCardDraft]
    settled_skips: list[DraftSkip]
    pending_retries: list[RetryWork]
    active_retry: RetryWork | None
    retry_route: str
