# 노드: 카드 초안을 검증해 통과/재작성/폐기로 판정한다.
#
# 검증은 세 겹이다. (1) 코드로 구조적 필수 조건, (2) 코드로 카드의 수치/날짜가 근거 문서에 있는지
# 교차검사, (3) LLM이 주장 단위로 근거와 대조한다. LLM은 "근거와 일관된가"라는 일반 질문에는 거의
# 항상 pass를 줘서(지어낸 수치, 지어낸 원인, 반대로 바꾼 결정도 통과) 안전망이 되지 못했다. 그래서
# "근거 없는 주장 목록"을 먼저 쓰게 하고(unsupported_claims), 목록이 비어 있지 않으면 코드가 pass를
# 막는다. 수치 교차검사는 LLM과 무관하게 결정적이다.
#
# 코드로 구조적 필수 조건을 먼저 검사한다 — 결정론적이고 LLM보다 싸고 확실해서, 비싼 LLM 호출
# 전에 먼저 거른다(extract의 토큰 안전장치와 같은 패턴). 통과한 초안만 LLM에게 맥락상 타당한지
# 묻는다. rewrite일 때 draft_card로 돌아가는 재시도 루프는 이 노드의 일이 아니라 graph.py가
# 처리한다.
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.logging import summarize_error
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import CardDraft, VerificationResult, VerifiedCard
from app.domains.documents.pipeline.stage import finish_stage
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "verify.md").read_text(encoding="utf-8")

# LLM에게 보여 줄 원문 섹션의 최대 글자 수. 섹션이 아주 길어도 검증 호출이 커지지 않게 한다
_MAX_SOURCE_CHARS = 12_000

# 수치 교차검사 대상 필드와 규칙. 한 자리 수("3종", "2가지")는 개수를 세어 쓴 파생 표현이
# 흔해서 제외하고, 두 자리 이상 숫자와 날짜만 본다(실제 카드 32개에 적용해 오탐 0건 확인)
_NUMBER_FIELDS = (
    "summary",
    "context",
    "problem",
    "cause",
    "approach",
    "rationale",
    "outcome",
    "lesson",
)
_MIN_NUMBER_DIGITS = 2
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")

# docs/schema.md "구조적 필수 조건" 그대로
_REQUIRED_FIELDS = ("title", "summary", "context", "lesson")
_CONDITIONAL_FIELDS = ("problem", "cause", "approach", "rationale", "outcome")


class _ClaimCheck(BaseModel):
    # 카드의 주장 하나와, 그것을 뒷받침한다고 모델이 찾은 원문 문장. 인용이 실제 원문에 있는지는
    # 코드가 확인하므로 모델이 "확인된다"고 말만 해서는 통과할 수 없다
    field: str
    claim: str
    quote: str
    quote_role: Literal["사실", "결정", "추정·예상·가정", "채택하지 않은 대안", "원문에 없음"]
    hedged: bool


class _Judgement(BaseModel):
    # LLM 구조화 출력은 필드 순서대로 생성된다. 주장별 인용 검사를 먼저 쓰게 하고, 이유, 판정 순으로
    # 써서 "일단 pass라고 정한 뒤 이유를 끼워 맞추는" 걸 막는다. 공개 계약인 VerificationResult는
    # 바꾸지 않고, 근거 없는 주장 목록은 reason에 합쳐서 내보낸다
    checks: list[_ClaimCheck]
    reason: str
    verdict: Literal["pass", "rewrite", "discard"]


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


def _source_text(draft: CardDraft, chunks: list) -> str:
    # 카드 근거가 속한 문서 전체 텍스트. 수치 교차검사의 기준이다
    # (문서 일부만 보면 정상 수치가 오탐된다)
    document_ids = {str(e.document_id) for e in draft.evidences}
    return "\n".join(c["text"] for c in chunks if str(c["document_id"]) in document_ids)


def _source_sections(draft: CardDraft, chunks: list) -> str:
    # 근거 발췌문이 속한 섹션 전문. 발췌문만 보면 사실인 주장도 "근거 없음"으로
    # 보이므로 LLM에게 같이 준다
    wanted = {(str(e.document_id), e.location) for e in draft.evidences}
    sections = [c for c in chunks if (str(c["document_id"]), c["location"] or None) in wanted]
    sections.sort(key=lambda c: (str(c["document_id"]), c["index"]))
    text = "\n\n".join(f"({c['location']})\n{c['text']}" for c in sections)
    return text[:_MAX_SOURCE_CHARS]


def _numbers(text: str) -> set[str]:
    return {token.replace(",", "") for token in _NUMBER_RE.findall(text)}


def _ungrounded_numbers(draft: CardDraft, source: str) -> list[str]:
    # 카드 본문의 두 자리 이상 수치와 날짜 중 근거 문서에 없는 것.
    # 문서 텍스트를 못 구하면 검사하지 않는다
    if not source.strip():
        return []
    known_numbers, known_dates = _numbers(source), set(_DATE_RE.findall(source))
    found: list[str] = []
    for field in _NUMBER_FIELDS:
        text = getattr(draft, field) or ""
        candidates = [d for d in _DATE_RE.findall(text) if d not in known_dates]
        candidates += [
            n
            for n in sorted(_numbers(text))
            if len(n.replace(".", "")) >= _MIN_NUMBER_DIGITS and n not in known_numbers
        ]
        found += [c for c in candidates if c not in found]
    return found


def _format_message(draft: CardDraft, chunks: list, ungrounded: list[str]) -> str:
    message = _format_draft(draft)
    sections = _source_sections(draft, chunks)
    if sections:
        message += f"\n\n## 원문 섹션\n{sections}"
    if ungrounded:
        message += (
            "\n\n## 자동 점검\n카드에 있는 다음 수치/날짜가 근거 문서 어디에도 없습니다: "
            + ", ".join(ungrounded)
        )
    return message


# 인용 비교는 글자와 숫자만 본다. 모델이 인용하면서 ·, →, —, 마크다운 기호, 줄바꿈을 빼거나 공백으로
# 바꾸는 일이 흔한데(예: "레지스트리·상세 명세" -> "레지스트리 상세 명세"), 같은 문장이다
_NON_TEXT_RE = re.compile(r"[\W_]+")
# 인용이 원문과 정확히 일치하지 않아도, 인용을 8글자씩 자른 조각 중 이 비율 이상이 원문에 있으면
# 같은 문장으로 본다(단어 몇 개를 빼거나 바꾼 인용). 긴 문서에서도 빠르도록 조각 검색만 쓴다
# (difflib.SequenceMatcher는 반복 글자가 많은 한글 긴 문서에서 수 분이 걸렸다)
_QUOTE_MATCH_RATIO = 0.85
_QUOTE_SHINGLE = 8
# 이보다 긴 인용은 존재 여부를 확인하지 않는다. 모델이 "한 문장만 인용하라"는 지시를 무시하고 섹션
# 통째로 붙여 넣는 일이 잦기 때문이다. 또 인용이 원문과 안 맞는 것만으로는 한두 건에
# 카드를 탈락시키지
# 않는다: 요약처럼 여러 문장을 합성한 주장은 모델이 인용 대신 의역해서 정상 카드도 걸리기 때문이다
# (엄격히 따졌을 때 실제 카드 32개 중 정상 카드 5개가 오탐). 절반 이상의 주장에서
# 인용이 맞지 않을 때만
# 문제로 본다. 라벨 기반 판정(원문에 없음, 채택하지 않은 대안, 추정·예상)은 엄격하게 유지한다
_MAX_VERIFIED_QUOTE_CHARS = 300
_MISSING_QUOTE_LIMIT = 0.5


def _plain(text: str) -> str:
    return _NON_TEXT_RE.sub("", text)


def _quote_in_source(quote: str, source: str) -> bool:
    if quote in source:
        return True
    if len(quote) <= _QUOTE_SHINGLE:
        return False
    shingles = [quote[i : i + _QUOTE_SHINGLE] for i in range(len(quote) - _QUOTE_SHINGLE + 1)]
    return sum(shingle in source for shingle in shingles) >= _QUOTE_MATCH_RATIO * len(shingles)


def _unsupported_claims(checks: list[_ClaimCheck], source: str) -> list[str]:
    # 모델이 달아 온 주장별 인용을 코드로 점검한다. source가 비어 있으면(원문을 못 구함) 인용 존재
    # 확인은 건너뛰고 라벨만 본다
    plain_source = _plain(source)
    unsupported = []  # 라벨로 확인된 문제: 항상 근거 없는 주장
    missing_quote = []  # 인용이 원문과 안 맞는 주장: 절반 이상일 때만 문제
    for check in checks:
        quote = _plain(check.quote)
        if check.quote_role == "원문에 없음" or not quote:
            if check.field == "lesson":
                # lesson은 경험에서 일반화한 시사점이라 원문에 같은 문장이 없는 게 정상이다
                # (실제 카드 32개에서 정상 카드 2개가 오탐). 수치는 _ungrounded_numbers가 따로 본다
                continue
            why = "근거 문장을 찾지 못함"
        elif check.quote_role == "채택하지 않은 대안":
            why = "채택하지 않은 대안을 결정처럼 씀"
        elif check.quote_role == "추정·예상·가정" and not check.hedged:
            why = "원문의 추정/예상/가정을 확정 사실처럼 씀"
        elif (
            plain_source
            and len(quote) <= _MAX_VERIFIED_QUOTE_CHARS
            and not _quote_in_source(quote, plain_source)
        ):
            missing_quote.append(f"{check.field}: {check.claim} — 인용한 문장이 원문에 없음")
            continue
        else:
            continue
        unsupported.append(f"{check.field}: {check.claim} — {why}")
    if len(missing_quote) >= _MISSING_QUOTE_LIMIT * max(len(checks), 1):
        unsupported += missing_quote
    return unsupported


def _finalize(judgement, ungrounded: list[str], source: str) -> VerificationResult:
    # LLM 판정에 코드 규칙을 얹는다:
    # 근거 없는 주장이나 근거 문서에 없는 수치가 있으면 pass가 될 수 없다
    claims = _unsupported_claims(list(getattr(judgement, "checks", None) or []), source)
    verdict, reason = judgement.verdict, judgement.reason
    parts = []
    if ungrounded:
        parts.append(f"근거 문서에 없는 수치/날짜: {', '.join(ungrounded)}")
    if claims:
        parts.append("근거 없는 주장: " + " / ".join(claims))
    if parts:
        reason = " | ".join([*parts, reason])
        if verdict == "pass":
            verdict = "rewrite"
    return VerificationResult(reason=reason, verdict=verdict)


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
    llm = get_llm().with_structured_output(_Judgement)
    chunks = state.get("chunks", [])

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
        source = _source_text(draft, chunks)
        ungrounded = _ungrounded_numbers(draft, source)
        try:
            judgement = invoke_with_retry(
                llm,
                [SystemMessage(_PROMPT), HumanMessage(_format_message(draft, chunks, ungrounded))],
            )
        except Exception as e:
            # 검증하지 못한 카드는 통과시키지 않고 건너뛴다(검증 안 된 카드가 저장되면 안 된다).
            # 나머지 카드는 계속 검증한다(stage.finish_stage 참고)
            logger.warning("verify: 카드 건너뜀 %r: %s", draft.title, summarize_error(e))
            failures.append(f"카드 검증 실패 '{draft.title}': {summarize_error(e)}")
            continue

        llm_verified += 1
        verified_cards.append(
            VerifiedCard(draft=draft, verification=_finalize(judgement, ungrounded, source))
        )

    _log_verdicts(verified_cards)
    return finish_stage(state, "verified_cards", verified_cards, failures, succeeded=llm_verified)
