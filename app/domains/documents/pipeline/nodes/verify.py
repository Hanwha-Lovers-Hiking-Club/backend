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
#   필수 필드에 문제 -> held(자동 재작성 없이 사람 검토. 오탐이면 다시 써도 같은 이유로 떨어진다)
#   호출 실패·판정 누락 -> incomplete(저장 안 함, job 경고)
# 공개 상태: passed이고 outcome이 있으면 active, 결과 없는 passed와 held는 draft.
import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import summarize_error
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import (
    CardDraft,
    ClaimCheck,
    ClaimChecks,
    Experience,
    VerificationResult,
    VerifiedCard,
)
from app.domains.documents.pipeline.stage import finish_stage
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "verify.md").read_text(encoding="utf-8")

MAX_WORKERS = 4
# 청크 원문이 아주 많아도 검증 호출이 커지지 않게 자른다
_MAX_CHUNK_CHARS = 30_000
_REQUIRED = ("title", "summary", "context", "lesson")
_CONDITIONAL = ("problem", "cause", "approach", "rationale", "outcome")
_OPTIONAL = ("technologies", "project_name", "department", "project_date")
_FIELDS = (*_REQUIRED[:3], *_CONDITIONAL, "lesson", *_OPTIONAL)
_OK = {"supported", "lesson_within_scope"}
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


def _prune(draft: CardDraft, cleared: list[str]) -> CardDraft:
    # 문제 필드를 비우고, 남은 필드가 인용하지 않는 근거는 빼서 근거 번호를 다시 매긴다
    update: dict = {f: ([] if f == "technologies" else None) for f in cleared}
    keep = {f: idx for f, idx in draft.field_sources.items() if f not in cleared}
    used = sorted({i for idx in keep.values() for i in idx})
    remap = {old: new for new, old in enumerate(used)}
    update["evidences"] = [draft.evidences[i] for i in used]
    update["field_sources"] = {f: [remap[i] for i in idx] for f, idx in keep.items()}
    return draft.model_copy(update=update)


def _decide(draft: CardDraft, checks: list[ClaimCheck]) -> VerifiedCard:
    present = [f for f in _FIELDS if _value(draft, f)]
    missing = [f for f in present if f not in {c.field for c in checks}]
    if missing:
        result = VerificationResult(
            verdict="incomplete", reasons=[f"판정 누락: {', '.join(missing)}"]
        )
        return VerifiedCard(draft=draft, verification=result)

    problems = [c for c in checks if c.field in present and c.judgement not in _OK]
    reasons = [f"{c.field}: {c.judgement} — {c.claim} ({c.note})" for c in problems]
    cleared = list(dict.fromkeys(c.field for c in problems if c.field not in _REQUIRED))
    held = any(c.field in _REQUIRED for c in problems)
    pruned = _prune(draft, cleared)

    if not any(getattr(pruned, f) for f in _CONDITIONAL):
        verdict, status = "excluded", None
        reasons.append("문제 필드를 비우니 조건부 필드(problem~outcome)가 모두 비었음")
    elif held:
        verdict, status = "held", "draft"
    else:
        verdict, status = "passed", "active" if pruned.outcome else "draft"
        if not pruned.outcome:
            reasons.append("결과(outcome) 없음: 내용은 통과, 공개 정책상 draft")
    result = VerificationResult(
        verdict=verdict, reasons=reasons, cleared_fields=cleared, status=status
    )
    return VerifiedCard(draft=pruned, verification=result)


def verify_one(
    draft: CardDraft, experience: Experience | None, chunks: dict, names: dict, llm
) -> VerifiedCard:
    """카드 하나를 검증한다. LLM 호출이 실패하면 예외를 그대로 던진다."""
    message = _Input(draft, experience, chunks, names).message()
    result: ClaimChecks = invoke_with_retry(llm, [SystemMessage(_PROMPT), HumanMessage(message)])
    return _decide(draft, result.checks)


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

    # 스레드에는 contextvar(job_id 로그·사용량 집계)가 넘어가지 않아서 호출마다 복사해 넘긴다
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            pool.submit(
                contextvars.copy_context().run, verify_one, d, experience_of(d), chunks, names, llm
            )
            for d in drafts
        ]

    verified: list[VerifiedCard] = []
    failures: list[str] = []
    for draft, future in zip(drafts, futures):
        try:
            card = future.result()
        except Exception as e:
            # 검증하지 못한 카드는 저장하지 않는다(내용 보류와 구분해 incomplete로 남긴다)
            card = VerifiedCard(
                draft=draft,
                verification=VerificationResult(
                    verdict="incomplete", reasons=[f"검증 호출 실패: {summarize_error(e)}"]
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
