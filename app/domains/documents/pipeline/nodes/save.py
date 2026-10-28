# 노드: embed를 통과한 카드를 knowledge_cards 도메인 service를 거쳐 저장한다.
#
# documents 도메인이 knowledge_cards 테이블을 직접 만지지 않는다(repository.py 주석 참고) —
# knowledge_cards.service.create_card()를 거친다. pydantic 모델을 그대로 넘기지 않고 plain dict로
# 변환해서 넘긴다(두 도메인이 서로의 내부 타입을 몰라도 되게).
from app.domains.documents.pipeline.models import CardDraft, CardEmbeddings
from app.domains.documents.pipeline.state import IngestionState
from app.domains.knowledge_cards import service as knowledge_cards_service


def _to_card_dict(draft: CardDraft, embeddings: CardEmbeddings) -> dict:
    return {
        "title": draft.title,
        "summary": draft.summary,
        "context": draft.context,
        "problem": draft.problem,
        "cause": draft.cause,
        "approach": draft.approach,
        "rationale": draft.rationale,
        "outcome": draft.outcome,
        "lesson": draft.lesson,
        "technologies": draft.technologies,
        "keywords": draft.keywords,
        "project_name": draft.project_name,
        "department": draft.department,
        "project_date": draft.project_date.isoformat() if draft.project_date else None,
        # MVP 범위에서는 저장되는 즉시 active로 둔다 (카드 품질 사후관리는 MVP 이후 범위)
        "status": "active",
        "content_embedding": embeddings.content_embedding,
        "situation_embedding": embeddings.situation_embedding,
        "solution_embedding": embeddings.solution_embedding,
        "lesson_embedding": embeddings.lesson_embedding,
    }


def _to_evidence_dicts(draft: CardDraft) -> list[dict]:
    return [
        {
            "document_id": str(evidence.document_id),
            "location": evidence.location,
            "excerpt": evidence.excerpt,
        }
        for evidence in draft.evidences
    ]


def save(state: IngestionState) -> dict:
    for embedded in state["embedded_cards"]:
        card = _to_card_dict(embedded.draft, embedded.embeddings)
        evidences = _to_evidence_dicts(embedded.draft)
        try:
            knowledge_cards_service.create_card(card, evidences)
        except Exception as e:
            # 이미 저장된 카드는 롤백하지 않는다(카드마다 별도 insert라 묶어서 되돌릴 수 없음)
            return {"error": f"카드 저장 실패: {e}"}

    return {}
