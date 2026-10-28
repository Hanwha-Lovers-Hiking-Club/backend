"""Check evidence against code-owned DocumentIR, never against LLM-created quotes."""

from app.domains.documents.pipeline.extraction import experience_id
from app.domains.documents.pipeline.verification.structure_checker import issue


def quote_matches(ref, block):
    if ref.start_char is None:
        return ref.excerpt in block.text
    return (
        ref.end_char <= len(block.text) and block.text[ref.start_char : ref.end_char] == ref.excerpt
    )


def check_evidence(draft, documents, extraction_result):
    issues, records = [], {}
    source = draft.source_extraction
    if experience_id(source) != draft.experience_id:
        issues.append(
            issue(
                "evidence",
                "EXPERIENCE_ID_MISMATCH",
                "experience_id",
                "Candidate key does not match the retained extraction",
                target="draft_card",
            )
        )
    current = [] if extraction_result is None else extraction_result.experiences
    if not any(item == source for item in current):
        issues.append(
            issue(
                "evidence",
                "EXTRACTION_SNAPSHOT_MISMATCH",
                "source_extraction",
                "Draft is not bound to the current extraction",
                target="extract",
            )
        )
    by_id = {document.document_id: document for document in documents}
    if len(by_id) != len(documents):
        return [
            issue(
                "evidence",
                "DUPLICATE_DOCUMENT",
                "documents",
                "Document IDs are not unique",
                target="normalize",
            )
        ], records
    for ref_id, ref in source.evidence_index.items():
        field = f"evidence_index.{ref_id}"
        document = by_id.get(ref.document_id)
        if document is None:
            issues.append(
                issue(
                    "evidence",
                    "DOCUMENT_NOT_FOUND",
                    field,
                    "Original document is unavailable",
                    target="parse",
                )
            )
            continue
        blocks = {block.block_id: block for block in document.blocks}
        block = blocks.get(ref.block_id)
        original = document.evidence_index.get(ref_id)
        if block is None or original is None or block.evidence_id != ref_id:
            issues.append(
                issue(
                    "evidence",
                    "EVIDENCE_NOT_FOUND",
                    field,
                    "Evidence ID does not identify an original block",
                    target="extract",
                )
            )
            continue
        if original.location != block.location or not quote_matches(original, block):
            issues.append(
                issue(
                    "evidence",
                    "QUOTE_MISMATCH",
                    field,
                    "Normalized evidence does not match its source block",
                    target="normalize",
                )
            )
            continue
        if (
            ref.document_id != block.document_id
            or original.block_id != ref.block_id
            or ref.location != original.location
            or ref.location != block.location
            or ref.excerpt != original.excerpt
        ):
            issues.append(
                issue(
                    "evidence",
                    "EVIDENCE_SOURCE_MISMATCH",
                    field,
                    "Evidence location or quotation differs from its source",
                    target="extract",
                )
            )
            continue
        if not quote_matches(ref, block):
            issues.append(
                issue(
                    "evidence",
                    "QUOTE_MISMATCH",
                    field,
                    "Quote is not an exact source interval",
                    target="extract",
                )
            )
            continue
        records[ref_id] = {
            "evidence": ref.model_dump(mode="json"),
            "block": block.model_dump(mode="json"),
        }
        if document.warnings:
            issues.append(
                issue(
                    "evidence",
                    "INCOMPLETE_SOURCE",
                    field,
                    "Source has unread/untranscribed content: " + "; ".join(document.warnings),
                    disposition="needs_review",
                    action="Inspect the original; OCR/vision is not automatic",
                )
            )
    return issues, records
