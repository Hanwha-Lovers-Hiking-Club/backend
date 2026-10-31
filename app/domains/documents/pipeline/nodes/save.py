# 노드: embed를 통과한 카드를 knowledge_cards 도메인 service를 거쳐 저장한다.
#
# documents 도메인이 knowledge_cards 테이블을 직접 만지지 않는다(repository.py 주석 참고) —
# knowledge_cards.service.create_card()를 거친다. pydantic 모델을 그대로 넘기지 않고 plain dict로
# 변환해서 넘긴다(두 도메인이 서로의 내부 타입을 몰라도 되게).
import logging

from app.core.logging import summarize_error
from app.domains.documents.pipeline.models import CardDraft, CardEmbeddings
from app.domains.documents.pipeline.state import IngestionState
from app.domains.knowledge_cards import service as knowledge_cards_service

logger = logging.getLogger(__name__)


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
    # 카드는 서로 독립적이라 하나가 저장에 실패해도 나머지는 계속 저장한다
    # (이미 목록에 노출된 카드를 되돌리는 것보다 안전하고, 카드 만드는 데 든 LLM 비용을 살린다).
    # 실패한 카드는 warnings에 남기고,
    # 저장하려던 카드가 전부 실패했을 때만 error로 job을 실패시킨다.
    # 카드가 하나도 없는 것(embedded_cards가 빈 목록)은 정상이다.
    saved = 0
    failures: list[str] = []
    for embedded in state["embedded_cards"]:
        card = _to_card_dict(embedded.draft, embedded.embeddings)
        evidences = _to_evidence_dicts(embedded.draft)
        try:
            knowledge_cards_service.create_card(card, evidences)
        except Exception as e:
            logger.warning("save: 카드 저장 실패 %r: %s", embedded.draft.title, summarize_error(e))
            failures.append(f"카드 저장 실패 '{embedded.draft.title}': {summarize_error(e)}")
            continue
        saved += 1

    logger.info("save: 카드 %d개 저장, %d개 실패", saved, len(failures))
    if failures and saved == 0:
        return {"saved_card_count": 0, "error": failures[0]}
    update: dict = {"saved_card_count": saved}
    if failures:
        update["warnings"] = [*state.get("warnings", []), *failures]
    return update
