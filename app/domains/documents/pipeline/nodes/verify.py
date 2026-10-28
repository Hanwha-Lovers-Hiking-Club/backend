# 노드: 카드 초안을 검증해 통과/재작성/폐기로 판정한다.
#
# 코드로 구조적 필수 조건을 먼저 검사한다 — 결정론적이고 LLM보다 싸고 확실해서, 비싼 LLM 호출
# 전에 먼저 거른다(extract의 토큰 안전장치와 같은 패턴). 통과한 초안만 LLM에게 맥락상 타당한지
# 묻는다. rewrite일 때 draft_card로 돌아가는 재시도 루프는 이 노드의 일이 아니라 graph.py가
# 처리한다.
from pathlib import Path

from app.domains.documents.pipeline.llm import get_llm
from app.domains.documents.pipeline.models import CardDraft, VerificationResult, VerifiedCard
from app.domains.documents.pipeline.state import IngestionState

_PROMPT = (Path(__file__).parent.parent / "prompts" / "verify.md").read_text(encoding="utf-8")

# docs/schema.md "구조적 필수 조건" 그대로
_REQUIRED_FIELDS = ("title", "summary", "context", "lesson")
_CONDITIONAL_FIELDS = ("problem", "cause", "approach", "rationale", "outcome")


def _check_structural(draft: CardDraft) -> str | None:
    missing = [f for f in _REQUIRED_FIELDS if not getattr(draft, f).strip()]
    if missing:
        return f"구조적 필수 조건 미충족: 필수 필드 비어있음 ({', '.join(missing)})"

    if not draft.evidences:
        return "구조적 필수 조건 미충족: 근거(evidences)가 1개도 없음"

    conditional_values = [getattr(draft, f) for f in _CONDITIONAL_FIELDS]
    if not any(v and v.strip() for v in conditional_values):
        return f"구조적 필수 조건 미충족: {'/'.join(_CONDITIONAL_FIELDS)} 전부 비어있음"

    return None


def _format_draft(draft: CardDraft) -> str:
    evidences = "\n".join(f"- ({e.location}) {e.excerpt}" for e in draft.evidences)
    return (
        f"title: {draft.title}\n"
        f"summary: {draft.summary}\n"
        f"context: {draft.context}\n"
        f"problem: {draft.problem}\n"
        f"cause: {draft.cause}\n"
        f"approach: {draft.approach}\n"
        f"rationale: {draft.rationale}\n"
        f"outcome: {draft.outcome}\n"
        f"lesson: {draft.lesson}\n"
        f"evidences:\n{evidences}"
    )


def verify(state: IngestionState) -> dict:
    llm = get_llm().with_structured_output(VerificationResult)

    verified_cards: list[VerifiedCard] = []
    for draft in state["card_drafts"]:
        structural_failure = _check_structural(draft)
        if structural_failure:
            verified_cards.append(
                VerifiedCard(
                    draft=draft,
                    verification=VerificationResult(verdict="discard", reason=structural_failure),
                )
            )
            continue

        prompt = f"{_PROMPT}\n{_format_draft(draft)}"
        try:
            verification = llm.invoke(prompt)
        except Exception as e:
            return {"error": f"카드 검증 실패: {e}"}

        verified_cards.append(VerifiedCard(draft=draft, verification=verification))

    return {"verified_cards": verified_cards}
