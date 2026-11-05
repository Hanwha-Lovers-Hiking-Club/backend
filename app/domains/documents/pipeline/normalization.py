# 추출한 텍스트를 바꾸지 않고 재현 가능한 원본 블록·근거 식별자를 부여한다.

import hashlib
import json
from uuid import uuid5

from app.domains.documents.pipeline.document import DocumentBlock, DocumentIR, ParsedDocument
from app.domains.documents.pipeline.models import EvidenceRef


def normalize_document(parsed: ParsedDocument) -> DocumentIR:
    blocks = []
    evidence_index = {}
    for order, raw in enumerate(parsed.blocks):
        canonical = json.dumps(raw.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        block_id = str(uuid5(parsed.document_id, f"block:{order}:{digest}"))
        evidence_id = str(uuid5(parsed.document_id, f"evidence:{block_id}:full"))
        block = DocumentBlock(
            **raw.model_dump(),
            document_id=parsed.document_id,
            block_id=block_id,
            evidence_id=evidence_id,
            order=order,
        )
        blocks.append(block)
        evidence_index[evidence_id] = EvidenceRef(
            document_id=parsed.document_id,
            block_id=block_id,
            location=raw.location,
            excerpt=raw.text,
            start_char=0,
            end_char=len(raw.text),
        )
    return DocumentIR(
        document_id=parsed.document_id,
        original_filename=parsed.original_filename,
        blocks=blocks,
        evidence_index=evidence_index,
        metadata=parsed.metadata,
        warnings=parsed.warnings,
    )
