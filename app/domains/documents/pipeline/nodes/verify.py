# 노드: draft_card의 카드 후보마다 주장을 원문 근거와 대조해 판정하고, 공개 상태를 정한다.
#
# "근거가 이 주장을 지지하는가"라는 의미 판단은 LLM이 한다. LLM은 "근거와 일관된가" 같은 넓은
# 질문에는 거의 항상 통과를 줘서(기존 verify의 교훈), 필드를 주장 단위로 나누고 주장마다 정해진
# 판정 하나를 고르게 한다. 코드는 그 판정에 따라 필드를 비우거나 카드를 보류하고, 판정이 빠진
# 필드가 없는지 확인한다.
#
# 입력은 필드별 인용 + 그 경험에 배정된 근거 전체 + 근거가 나온 청크 원문이다. 카드가 인용한 근거만
# 보면 그 주장의 범위를 제한하는 다른 근거(다른 환경의 실패, 남은 실패 등)를 볼 수 없다.
# 수치 문자열 대조는 자동 탈락에 쓰지 않고 LLM에게 넘기는 확인 신호로만 쓴다(표기가 다른 같은 값,
# 같은 문자열의 다른 대상을 문자열로는 구분할 수 없다).
#
# 판정(models.VerificationResult 참고):
#   조건부·선택 필드에 문제 -> 그 필드를 비운다. 비운 뒤 조건부 필드가 모두 비면 excluded
#   필수 필드에 문제이거나 결론을 바꾸는 한계를 빠뜨림 -> held(자동 재작성 없이 사람 검토)
#   호출 실패·판정 누락 -> incomplete(저장 안 함, job 경고)
# 공개 상태: 카드는 확정된 정답이 아니라 "이 조건에서 이렇게 했고 여기까지 확인했다"는 경험
# 지식이다. 그래서 효과 확인 여부가 아니라 재사용 가치로 정한다. passed이고 적용 상황·행동·확인
# 범위가 있으면 결과가 없거나 미확정이어도 active, 그 밖의 passed와 held는 draft.
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import summarize_error
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import (
    CardDraft,
    ClaimCheck,
    ClaimChecks,
    Experience,
    OmittedLimit,
    ReuseCheck,
    VerificationResult,
    VerifiedCard,
)
from app.domains.documents.pipeline.stage import finish_stage, run_parallel
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "verify.md").read_text(encoding="utf-8")

MAX_WORKERS = 4
# 청크 원문이 아주 많아도 검증 호출이 커지지 않게 자른다
_MAX_CHUNK_CHARS = 30_000
_REQUIRED = ("title", "summary", "context", "lesson")
_CONDITIONAL = ("problem", "cause", "approach", "rationale", "outcome")
# 검증 메시지에 보여 주고 판정을 받는 필드(카드 필드 순서 그대로)
_FIELDS = (
    "title",
    "summary",
    "context",
    "problem",
    "cause",
    "approach",
    "rationale",
    "outcome",
    "lesson",
    "technologies",
    "department",
    "project_date",
)
_OK = {"supported", "lesson_within_scope"}
# 원문이 효과 판단을 보류한 관찰을 그대로 적었다는 뜻이다. 결과를 옮겨 적는 outcome과 summary에서만
# 문제가 아니다. 다른 필드에 붙으면 효과를 단정하는 주장이 통과하므로 문제로 본다
_UNCONFIRMED = "result_unconfirmed"
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _value(draft: CardDraft, field: str) -> str | None:
    value = getattr(draft, field)
    if field == "technologies":
        return ", ".join(value) or None
    if field == "project_date":
        return value.isoformat() if value else None
    return value or None


def _numbers(text: str) -> set[str]:
    # "1,204"와 "1204", "08"과 "8"을 같은 값으로 본다
    return {n.replace(",", "").lstrip("0") or "0" for n in _NUMBER_RE.findall(text)}


class _Input:
    # 검증 메시지를 만든다. 경험의 근거는 E1.., 경험 근거가 아닌 카드 근거(문서 머리말)는 H1..
    def __init__(
        self, draft: CardDraft, experience: Experience | None, chunks: dict[str, Chunk], names: dict
    ):
        self.draft, self.chunks, self.names = draft, chunks, names
        self.evidences = [
            e for e in (experience.evidences if experience else []) if e.chunk_id in chunks
        ]
        label = {(e.chunk_id, e.quote): f"E{i}" for i, e in enumerate(self.evidences, 1)}
        self.result_ids = {f"E{i}" for i, e in enumerate(self.evidences, 1) if e.kind == "result"}
        self.card_labels: list[str] = []
        self.headers: list[tuple[str, str, str]] = []  # (H id, chunk_id, 발췌문)
        for ev in draft.evidences:
            key = (ev.chunk_id, ev.excerpt)
            if key not in label:
                label[key] = f"H{len(self.headers) + 1}"
                self.headers.append((label[key], ev.chunk_id, ev.excerpt or ""))
            self.card_labels.append(label[key])

    def message(self) -> str:
        d = self.draft
        lines = ["## 카드"]
        for field in _FIELDS:
            value = _value(d, field)
            if value:
                refs = ", ".join(
                    dict.fromkeys(self.card_labels[i] for i in d.field_sources.get(field, []))
                )
                lines.append(f"{field}: {value}  [근거: {refs or '없음'}]")

        lines += ["", "## 경험의 근거 전체"]
        for i, e in enumerate(self.evidences, 1):
            c = self.chunks[e.chunk_id]
            where = f"{self.names[str(c['document_id'])]} · {c['location'] or '위치 없음'}"
            lines += [
                f"E{i} [{e.kind}] ({where})",
                f"  내용: {e.statement}",
                f"  원문: {e.quote}",
            ]
        for hid, chunk_id, excerpt in self.headers:
            c = self.chunks.get(chunk_id)
            where = self.names[str(c["document_id"])] if c else "?"
            lines.append(f"{hid} [문서 머리말] ({where})\n  원문: {excerpt}")

        lines += ["", "## 청크 원문"]
        chunk_ids = dict.fromkeys(
            [e.chunk_id for e in self.evidences] + [c for _, c, _ in self.headers]
        )
        source, used = [], 0
        for chunk_id in chunk_ids:
            c = self.chunks.get(chunk_id)
            if not c or used + len(c["text"]) > _MAX_CHUNK_CHARS:
                continue
            used += len(c["text"])
            source.append(c["text"])
            name = self.names[str(c["document_id"])]
            lines.append(f'<chunk document="{name}" location="{c["location"]}">')
            lines += [c["text"], "</chunk>"]

        known = _numbers("\n".join(source + [e.quote for e in self.evidences]))
        card = "\n".join(v for f in _FIELDS if (v := _value(d, f)))
        unknown = sorted(_numbers(card) - known, key=lambda n: (len(n), n))
        lines += ["", "## 자동 점검"]
        if unknown:
            lines.append(
                "카드의 다음 수치는 근거 원문에서 같은 문자열로 찾지 못했다"
                f"(직접 대조해 판단할 것): {', '.join(unknown)}"
            )
        else:
            lines.append(
                "카드의 수치는 모두 근거 원문에 같은 문자열로 있다(대상·단위는 직접 대조할 것)."
            )
        return "\n".join(lines)


def _is_ok(check: ClaimCheck, outcome_scoped: bool) -> bool:
    if check.judgement in _OK:
        return True
    if check.judgement == _UNCONFIRMED:
        return check.field in ("outcome", "summary")
    # 제목과 요약은 조건을 모두 담지 않아도 된다. 다만 본문 outcome에도 조건이 없으면
    # 그 범위가 카드 어디에도 없는 것이므로 문제로 남긴다
    if check.judgement == "condition_dropped" and check.field in ("title", "summary"):
        return outcome_scoped
    return False


def _is_reusable(reuse: ReuseCheck) -> bool:
    parts = (reuse.situation, reuse.guidance, reuse.confirmed_scope)
    return reuse.kind != "none" and all(part.strip() for part in parts)


def _prune(draft: CardDraft, cleared: list[str]) -> CardDraft:
    # 문제 필드를 비우고, 남은 필드가 인용하지 않는 근거는 빼서 근거 번호를 다시 매긴다
    update: dict = {f: ([] if f == "technologies" else None) for f in cleared}
    keep = {f: idx for f, idx in draft.field_sources.items() if f not in cleared}
    used = sorted({i for idx in keep.values() for i in idx})
    remap = {old: new for new, old in enumerate(used)}
    update["evidences"] = [draft.evidences[i] for i in used]
    update["field_sources"] = {f: [remap[i] for i in idx] for f, idx in keep.items()}
    return draft.model_copy(update=update)


def _omitted(limits: list[OmittedLimit], result_ids: set[str]) -> list[str]:
    return [o.limit for o in limits if o.evidence_id in result_ids and o.limit.strip()]


def _decide(draft: CardDraft, result: ClaimChecks, result_ids: set[str]) -> VerifiedCard:
    checks = result.checks
    present = [f for f in _FIELDS if _value(draft, f)]
    missing = [f for f in present if f not in {c.field for c in checks}]
    if missing:
        verification = VerificationResult(
            verdict="incomplete", reasons=[f"판정 누락: {', '.join(missing)}"]
        )
        return VerifiedCard(draft=draft, verification=verification)

    outcome_scoped = not any(
        c.field == "outcome" and c.judgement == "condition_dropped" for c in checks
    )
    problems = [c for c in checks if c.field in present and not _is_ok(c, outcome_scoped)]
    omitted = _omitted(result.omitted_limits, result_ids)
    reasons = [f"{c.field}: {c.judgement} — {c.claim} ({c.note})" for c in problems]
    reasons += [f"누락: {limit}" for limit in omitted]
    cleared = list(dict.fromkeys(c.field for c in problems if c.field not in _REQUIRED))
    held = bool(omitted) or any(c.field in _REQUIRED for c in problems)
    pruned = _prune(draft, cleared)

    if not any(getattr(pruned, f) for f in _CONDITIONAL):
        verdict, status = "excluded", None
        reasons.append("문제 필드를 비우니 조건부 필드(problem~outcome)가 모두 비었음")
    elif held:
        verdict, status = "held", "draft"
    else:
        reusable = _is_reusable(result.reuse)
        verdict, status = "passed", "active" if reusable else "draft"
        if not reusable:
            reasons.append("재사용 가치 부족: 내용은 통과, 공개 정책상 draft")
    verification = VerificationResult(
        verdict=verdict, reasons=reasons, cleared_fields=cleared, status=status
    )
    return VerifiedCard(draft=pruned, verification=verification)


def verify_one(
    draft: CardDraft, experience: Experience | None, chunks: dict, names: dict, llm
) -> VerifiedCard:
    """카드 하나를 검증한다. LLM 호출이 실패하면 예외를 그대로 던진다."""
    source = _Input(draft, experience, chunks, names)
    messages = [SystemMessage(_PROMPT), HumanMessage(source.message())]
    result: ClaimChecks = invoke_with_retry(llm, messages)
    return _decide(draft, result, source.result_ids)


def verify(state: IngestionState) -> dict:
    drafts = state.get("card_drafts", [])
    if not drafts:
        return {"verified_cards": []}
    experiences = state.get("experiences", [])
    chunks = {c["id"]: c for c in state["chunks"]}
    names = {str(d["id"]): d["original_filename"] for d in state["documents"]}
    llm = get_llm().with_structured_output(ClaimChecks)

    def experience_of(draft: CardDraft) -> Experience | None:
        i = draft.experience_index
        return experiences[i] if i is not None and 0 <= i < len(experiences) else None

    results = run_parallel(
        lambda d: verify_one(d, experience_of(d), chunks, names, llm), drafts, MAX_WORKERS
    )

    verified: list[VerifiedCard] = []
    failures: list[str] = []
    for draft, card in zip(drafts, results):
        if isinstance(card, Exception):
            # 검증하지 못한 카드는 저장하지 않는다(내용 보류와 구분해 incomplete로 남긴다)
            card = VerifiedCard(
                draft=draft,
                verification=VerificationResult(
                    verdict="incomplete", reasons=[f"검증 호출 실패: {summarize_error(card)}"]
                ),
            )
        verified.append(card)
        v = card.verification
        if v.verdict == "incomplete":
            failures.append(f"카드 검증 미완료 '{draft.title}': {v.reasons[0]}")
        log = logger.info if v.verdict == "passed" else logger.warning
        log("verify: %s(%s) %r %s", v.verdict, v.status, draft.title, " | ".join(v.reasons)[:500])

    counts = {
        k: sum(c.verification.verdict == k for c in verified)
        for k in ("passed", "held", "excluded", "incomplete")
    }
    logger.info("verify: 카드 %d개 -> %s", len(verified), counts)
    return finish_stage(
        state, "verified_cards", verified, failures, succeeded=len(verified) - len(failures)
    )
