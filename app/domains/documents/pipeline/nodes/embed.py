# 노드: verify를 통과한 카드만 kure-v3로 4종 임베딩(전체/상황/해결/교훈)을 만든다.
#
# 카드 1개당 kure 호출은 1번만 한다 — 4개 텍스트가 서로 다른 추론이 필요한 게 아니라 독립적인
# 문자열일 뿐이라, kure의 배치 입력(input=list)으로 왕복을 줄인다. 빈 필드(None)는 조합 텍스트에서
# 그 섹션을 통째로 생략한다("없음" 같은 의미 없는 텍스트가 벡터에 섞이지 않게).
import logging

from app.domains.documents.pipeline.embedding import embed_documents
from app.domains.documents.pipeline.models import CardDraft, CardEmbeddings, EmbeddedCard
from app.domains.documents.pipeline.stage import finish_stage
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)


def _build_text(role: str, sections: list[tuple[str, str | None]]) -> str:
    parts = [f"{label}:\n{value}" for label, value in sections if value]
    return f"[{role}]\n\n" + "\n\n".join(parts)


def _build_texts(draft: CardDraft) -> list[str]:
    return [
        _build_text(
            "전체 맥락",
            [
                ("제목", draft.title),
                ("요약", draft.summary),
                ("배경", draft.context),
                ("문제", draft.problem),
                ("원인", draft.cause),
                ("해결 방법", draft.approach),
                ("이유", draft.rationale),
                ("결과", draft.outcome),
                ("교훈", draft.lesson),
            ],
        ),
        _build_text("상황 및 문제", [("환경", draft.context), ("발생한 문제", draft.problem)]),
        _build_text(
            "대응 및 해결",
            [
                ("문제", draft.problem),
                ("원인", draft.cause),
                ("해결 방법", draft.approach),
                ("이유", draft.rationale),
                ("결과", draft.outcome),
            ],
        ),
        _build_text(
            "교훈", [("배경", draft.context), ("문제", draft.problem), ("교훈", draft.lesson)]
        ),
    ]


def embed(state: IngestionState) -> dict:
    embedded_cards: list[EmbeddedCard] = []
    failures: list[str] = []
    for verified in state["verified_cards"]:
        if verified.verification.verdict != "pass":
            continue

        try:
            content_vec, situation_vec, solution_vec, lesson_vec = embed_documents(
                _build_texts(verified.draft)
            )
        except Exception as e:
            # 임베딩이 없는 카드는 검색에 안 잡히므로 저장하지 않고 건너뛴다. 나머지는 계속 처리한다
            logger.warning("embed: 카드 건너뜀 %r: %s", verified.draft.title, e)
            failures.append(f"카드 임베딩 실패 '{verified.draft.title}': {e}")
            continue

        embedded_cards.append(
            EmbeddedCard(
                draft=verified.draft,
                embeddings=CardEmbeddings(
                    content_embedding=content_vec,
                    situation_embedding=situation_vec,
                    solution_embedding=solution_vec,
                    lesson_embedding=lesson_vec,
                ),
            )
        )

    logger.info("embed: 통과 카드 %d개 임베딩 완료", len(embedded_cards))
    return finish_stage(state, "embedded_cards", embedded_cards, failures)
