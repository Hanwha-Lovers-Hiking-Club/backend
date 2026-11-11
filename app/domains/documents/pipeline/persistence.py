"""파이프라인 내부 모델을 지식카드 서비스의 공개 dict 계약으로 변환한다."""

import math

from pydantic import ValidationError

from app.config import settings
from app.core.technology import normalize_technologies
from app.domains.documents.pipeline.document_lifecycle import DocumentSaveError
from app.domains.documents.pipeline.processing import DocumentSaveRequest, SaveReceipt
from app.domains.knowledge_cards import service
from app.domains.knowledge_cards.contracts import CardPersistenceError

EMBEDDING_COLUMNS = {
    "content": "content_embedding",
    "situation": "situation_embedding",
    "solution": "solution_embedding",
    "lesson": "lesson_embedding",
}


def build_save_payload(request: DocumentSaveRequest) -> dict:
    """통과 카드와 실제 생성된 벡터만 변환한다. 내부 사실 상태·근거 ID는 외부에 강제하지 않는다."""
    request = DocumentSaveRequest.model_validate(request)
    document_id = request.document.document_id
    if len({card.experience_id for card in request.cards}) != len(request.cards):
        raise DocumentSaveError("Duplicate card experiences")
    vectors_by_experience = {item.experience_id: item for item in request.embeddings}
    if len(vectors_by_experience) != len(request.embeddings):
        raise DocumentSaveError("Duplicate card embeddings")
    if set(vectors_by_experience) - {card.experience_id for card in request.cards}:
        raise DocumentSaveError("Embedding has no matching card")

    rows = []
    for card in request.cards:
        if any(
            ref.document_id != document_id for ref in card.source_extraction.evidence_index.values()
        ):
            raise DocumentSaveError("Card references a different document")
        evidence_ids = dict.fromkeys(
            evidence_id for refs in card.field_evidence_refs.values() for evidence_id in refs
        )
        if not evidence_ids:
            raise DocumentSaveError("Card has no source evidence")
        embedding = vectors_by_experience.get(card.experience_id)
        if embedding is not None and (
            embedding.model != settings.EMBEDDING_MODEL
            or embedding.dimensions != settings.EMBEDDING_DIMENSIONS
            or set(embedding.vectors) != set(embedding.input_hashes)
            or any(
                len(vector) != settings.EMBEDDING_DIMENSIONS
                or not all(type(value) in (int, float) and math.isfinite(value) for value in vector)
                or not any(value != 0 for value in vector)
                for vector in embedding.vectors.values()
            )
        ):
            raise DocumentSaveError("Embedding does not match configured model")
        row = card.to_card_fields()
        # 카드 검색·챗봇·화면 표시는 technologies를 표준 ID로 다룬다(app/core/technology).
        # 검증을 마친 원문 표기를 저장 직전에 같은 규칙으로 정규화한다.
        row["technologies"] = normalize_technologies(row.get("technologies") or [])
        row["status"] = "active"
        row["evidences"] = [
            {
                "document_id": str(document_id),
                "location": card.source_extraction.evidence_index[evidence_id].location,
                "excerpt": card.source_extraction.evidence_index[evidence_id].excerpt,
            }
            for evidence_id in evidence_ids
        ]
        for view, column in EMBEDDING_COLUMNS.items():
            row[column] = embedding.vectors.get(view) if embedding is not None else None
        rows.append(row)

    return {
        "document_id": str(document_id),
        "payload_hash": request.payload_hash,
        "cards": rows,
    }


async def save_document(request: DocumentSaveRequest) -> SaveReceipt:
    """실제 저장 서비스의 확인과 오류를 파이프라인 계약으로 되돌린다. fake 대체는 하지 않는다."""
    try:
        payload = build_save_payload(request)
        receipt = await service.save_document_cards(payload)
        return SaveReceipt.model_validate(receipt)
    except CardPersistenceError as exc:
        raise DocumentSaveError(str(exc)) from exc
    except ValidationError as exc:
        raise DocumentSaveError("Invalid pipeline save contract") from exc
