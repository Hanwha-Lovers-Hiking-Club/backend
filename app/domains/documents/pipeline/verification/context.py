"""카드의 인용 근거와 구분되는 원문 문맥을 문자 예산 안에서 선택한다."""

import json


def serialized_size(value):
    return len(json.dumps(value, ensure_ascii=False))


def build_review_context(draft, records, documents, *, max_chars, request_budget, base_payload):
    """작은 문서는 전체를, 큰 문서는 인용 근처의 온전한 블록을 제공한다. 블록을 자르지 않는다."""
    ids = {ref.document_id for ref in draft.source_extraction.evidence_index.values()}
    scoped = {doc.document_id: doc for doc in documents if doc.document_id in ids}
    cited = set(records)
    context = {
        "documents": {
            str(doc_id): {
                "coverage": "unavailable" if doc_id not in scoped else "partial",
                "total_blocks": len(scoped[doc_id].blocks) if doc_id in scoped else None,
            }
            for doc_id in sorted(ids, key=str)
        },
        "blocks": {},
    }

    def update_coverage():
        for doc_id, doc in scoped.items():
            covered = cited | context["blocks"].keys()
            context["documents"][str(doc_id)]["coverage"] = (
                "complete" if all(b.evidence_id in covered for b in doc.blocks) else "partial"
            )

    def fits():
        return (
            serialized_size(context) <= max_chars
            and serialized_size({**base_payload, "review_context": context}) <= request_budget
        )

    update_coverage()
    # 문맥 예산 0은 인용 근거만 사용하는 비교 조건이다. 메타데이터도 요청에 추가하지 않는다.
    if not max_chars or not fits():
        return None
    candidates = []
    for doc_id, doc in scoped.items():
        anchors = [b.order for b in doc.blocks if b.evidence_id in cited]
        for block in doc.blocks:
            if block.evidence_id not in cited:
                distance = min((abs(block.order - x) for x in anchors), default=block.order)
                candidates.append((distance, str(doc_id), block.order, block))
    for _, doc_id, _, block in sorted(candidates, key=lambda item: item[:3]):
        context["blocks"][block.evidence_id] = {
            "document_id": doc_id,
            "block_id": block.block_id,
            "location": block.location,
            "text": block.text,
        }
        if not fits():
            del context["blocks"][block.evidence_id]
    update_coverage()
    # complete/partial 두 표기의 길이가 달라질 수 있으므로 최종 크기도 검사한다.
    if not fits():
        return None
    return context
