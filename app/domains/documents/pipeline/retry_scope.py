# 재처리 대상의 문서·근거 청크 범위를 실행 로직과 예산 계산에서 동일하게 사용한다.


def retry_document_ids(work):
    return {ref.document_id for ref in work.draft.source_extraction.evidence_index.values()}


def retry_chunks(work, chunks):
    ids = retry_document_ids(work)
    selected = [item for item in chunks if item.document_id in ids]
    if work.request.target_node == "extract":
        refs = set(work.draft.source_extraction.evidence_index)
        selected = [
            item for item in selected if any(block.evidence_id in refs for block in item.blocks)
        ]
    return selected
