# 노드: 카드 초안을 검증해 통과/재작성/폐기로 판정한다.
#
# 코드로 구조적 필수 조건을 먼저 검사한다 — 결정론적이고 LLM보다 싸고 확실해서, 비싼 LLM 호출
# 전에 먼저 거른다(extract의 토큰 안전장치와 같은 패턴). 통과한 초안만 LLM에게 맥락상 타당한지
# 묻는다. rewrite일 때 draft_card로 돌아가는 재시도 루프는 이 노드의 일이 아니라 graph.py가
# 처리한다.
import logging
from collections import Counter
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import CardDraft, VerificationResult, VerifiedCard
from app.domains.documents.pipeline.stage import finish_stage
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)

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
    # null/빈 값을 "None"으로 찍으면 LLM이 실제 값처럼 다룰 수 있어서 "(없음)"으로 표시한다
    def show(value: str | None) -> str:
        return value.strip() if value and value.strip() else "(없음)"

    evidences = "\n".join(f"- ({e.location}) {e.excerpt}" for e in draft.evidences)
    return (
        f"title: {draft.title}\n"
        f"summary: {draft.summary}\n"
        f"context: {draft.context}\n"
        f"problem: {show(draft.problem)}\n"
        f"cause: {show(draft.cause)}\n"
        f"approach: {show(draft.approach)}\n"
        f"rationale: {show(draft.rationale)}\n"
        f"outcome: {show(draft.outcome)}\n"
        f"lesson: {draft.lesson}\n"
        f"evidences:\n{evidences}"
    )


def _log_verdicts(verified_cards: list[VerifiedCard]) -> None:
    counts = Counter(v.verification.verdict for v in verified_cards)
    logger.info(
        "verify: pass %d / rewrite %d / discard %d",
        counts["pass"],
        counts["rewrite"],
        counts["discard"],
    )
    # rewrite/discard는 저장되지 않으므로 왜 버려졌는지 남긴다
    for v in verified_cards:
        if v.verification.verdict != "pass":
            logger.warning(
                "verify: %s %r - %s", v.verification.verdict, v.draft.title, v.verification.reason
            )


def verify(state: IngestionState) -> dict:
    llm = get_llm().with_structured_output(VerificationResult)

    verified_cards: list[VerifiedCard] = []
    failures: list[str] = []
    llm_verified = 0  # LLM 검증에 성공한 카드 수(구조 검사에서 탈락한 카드는 호출이 없어 제외)
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

        # 지시문은 시스템 메시지, 카드 초안(원문 근거 포함)은 사용자 메시지로 분리해서
        # 근거 속 문장이 지시문이나 판정과 섞이지 않게 한다
        try:
            verification = invoke_with_retry(
                llm, [SystemMessage(_PROMPT), HumanMessage(_format_draft(draft))]
            )
        except Exception as e:
            # 검증하지 못한 카드는 통과시키지 않고 건너뛴다(검증 안 된 카드가 저장되면 안 된다).
            # 나머지 카드는 계속 검증한다(stage.finish_stage 참고)
            logger.warning("verify: 카드 건너뜀 %r: %s", draft.title, e)
            failures.append(f"카드 검증 실패 '{draft.title}': {e}")
            continue

        llm_verified += 1
        verified_cards.append(VerifiedCard(draft=draft, verification=verification))

    _log_verdicts(verified_cards)
    return finish_stage(state, "verified_cards", verified_cards, failures, succeeded=llm_verified)
