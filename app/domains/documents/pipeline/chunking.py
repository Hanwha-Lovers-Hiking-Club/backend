# 섹션 경계를 기준으로 청크를 구성하고 코드·로그·표 블록과 원본 식별자를 보존한다.

from uuid import uuid5

from app.domains.documents.pipeline.document import Chunk, ChunkOptions, DocumentIR


def chunk_document(document: DocumentIR, options: ChunkOptions | None = None) -> list[Chunk]:
    options = options or ChunkOptions()
    chunks = []
    pending = []

    def flush(section_path=None):
        if not pending:
            return
        text = "\n\n".join(block.text for block in pending)
        chunks.append(
            Chunk(
                document_id=document.document_id,
                chunk_id=str(uuid5(document.document_id, ":".join(b.block_id for b in pending))),
                blocks=list(pending),
                text=text,
                section_path=pending[0].section_path if section_path is None else section_path,
                oversized=len(text) > options.target_chars,
            )
        )
        pending.clear()

    total_chars = sum(len(block.text) for block in document.blocks) + 2 * max(
        0, len(document.blocks) - 1
    )
    if (
        document.blocks
        and options.whole_document_chars
        and total_chars <= options.whole_document_chars
    ):
        # 섹션별 경로는 각 블록에 보존하고, 전체 청크에는 공통 제목 경로만 표시한다.
        common_path = list(document.blocks[0].section_path)
        for block in document.blocks[1:]:
            while common_path and block.section_path[: len(common_path)] != common_path:
                common_path.pop()
        pending.extend(document.blocks)
        flush(common_path)
        return chunks

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
