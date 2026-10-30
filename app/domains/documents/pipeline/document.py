# 파싱·정규화·청킹 단계의 데이터 모델과 옵션을 정의한다. API·DB·LLM 클라이언트는 포함하지 않는다.

from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.domains.documents.pipeline.models import EvidenceId, EvidenceRef, NonBlankText

BlockType = Literal["heading", "paragraph", "code", "log", "table", "list"]


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentSource(DocumentModel):
    """Internal local file reference. Upload/download and cleanup belong to service."""

    document_id: UUID
    original_filename: NonBlankText
    path: Path


class ParseOptions(DocumentModel):
    # Local parser resource bounds, not an external upload API contract.
    max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_expanded_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    max_sheet_cells: int = Field(default=200_000, gt=0)


class ChunkOptions(DocumentModel):
    # Soft character target. Atomic blocks can exceed it and are marked oversized.
    target_chars: int = Field(default=6000, gt=0)


class ParsedBlock(DocumentModel):
    block_type: BlockType
    text: NonBlankText
    location: NonBlankText
    section_path: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ParsedDocument(DocumentModel):
    document_id: UUID
    original_filename: NonBlankText
    blocks: list[ParsedBlock]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DocumentBlock(ParsedBlock):
    document_id: UUID
    block_id: NonBlankText
    evidence_id: EvidenceId
    order: int = Field(ge=0)


class DocumentIR(DocumentModel):
    document_id: UUID
    original_filename: NonBlankText
    blocks: list[DocumentBlock]
    evidence_index: dict[EvidenceId, EvidenceRef]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_block_identity(self) -> Self:
        ids = [block.block_id for block in self.blocks]
        refs = [block.evidence_id for block in self.blocks]
        if len(set(ids)) != len(ids) or len(set(refs)) != len(refs):
            raise ValueError("block and evidence IDs must be unique within a document")
        if set(refs) != self.evidence_index.keys():
            raise ValueError("document blocks and evidence registry must agree")
        for order, block in enumerate(self.blocks):
            ref = self.evidence_index[block.evidence_id]
            if block.document_id != self.document_id or block.order != order:
                raise ValueError("document ID and block order must agree")
            if ref.document_id != self.document_id or ref.block_id != block.block_id:
                raise ValueError("evidence must refer to its own document block")
        return self


class Chunk(DocumentModel):
    document_id: UUID
    chunk_id: NonBlankText
    blocks: list[DocumentBlock] = Field(min_length=1)
    text: NonBlankText
    section_path: list[str]
    oversized: bool = False
