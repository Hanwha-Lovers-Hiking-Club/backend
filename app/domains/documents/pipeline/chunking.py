"""Section-aware grouping. Preserve code/log/table blocks and source identity."""

from uuid import uuid5

from app.domains.documents.pipeline.document import Chunk, ChunkOptions, DocumentIR


def chunk_document(document: DocumentIR, options: ChunkOptions | None = None) -> list[Chunk]:
    options = options or ChunkOptions()
    chunks = []
    pending = []

    def flush():
        if not pending:
            return
        text = "\n\n".join(block.text for block in pending)
        chunks.append(
            Chunk(
                document_id=document.document_id,
                chunk_id=str(uuid5(document.document_id, ":".join(b.block_id for b in pending))),
                blocks=list(pending),
                text=text,
                section_path=pending[0].section_path,
                oversized=len(text) > options.target_chars,
            )
        )
        pending.clear()

    for block in document.blocks:
        length = sum(len(b.text) + 2 for b in pending) + len(block.text)
        section_changed = pending and block.section_path != pending[0].section_path
        # A heading is kept with its first following block even when that exceeds the target.
        only_heading = len(pending) == 1 and pending[0].block_type == "heading"
        if section_changed or (pending and length > options.target_chars and not only_heading):
            flush()
        pending.append(block)
    flush()
    return chunks
