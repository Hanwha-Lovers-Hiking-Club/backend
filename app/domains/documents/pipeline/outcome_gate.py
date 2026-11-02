# outcome 환각(예상·계획을 "확인된 결과"처럼 쓰는 것) 차단 장치. draft_card가 경험마다 호출한다.
#
# 모델에게 "예상은 결과가 아니다"라고 시키는 규칙은 무시되므로, 결과·효과 문장마다 상태 라벨을
# 달게 하고(좁은 질문), 코드가 confirmed만 골라 outcome을 다시 쓰게 한다. 나머지(expected/planned)는
# 버리지 않고 rationale 끝에 "기대한 효과"로 붙인다.
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.domains.documents.pipeline.chunk_formatting import format_chunks
from app.domains.documents.pipeline.llm import invoke_with_retry
from app.domains.documents.pipeline.models import OutcomeStatements, OutcomeText
from app.domains.documents.pipeline.state import Chunk

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).parent / "prompts"
_LABEL_PROMPT = (_PROMPTS / "outcome_labels.md").read_text(encoding="utf-8")
_WRITE_PROMPT = (_PROMPTS / "outcome_write.md").read_text(encoding="utf-8")

# ponytail: 기대 효과는 노이즈를 막으려고 앞의 3개만 붙인다. 모자라면 늘린다
MAX_EXPECTED_IN_RATIONALE = 3
_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]+")


def _normalize(text: str) -> str:
    # 모델이 ·, →, 마크다운 기호를 빼고 인용하는 일이 많아 글자·숫자만 비교한다
    return _NON_WORD.sub("", text)


def gate_outcome(llm, source_chunks: list[Chunk]) -> tuple[str | None, list[str]]:
    """(확인된 결과로 다시 쓴 outcome 또는 None, 기대·계획 문장 목록)을 돌려준다."""
    source = _normalize("".join(c["text"] for c in source_chunks))
    labels = invoke_with_retry(
        llm.with_structured_output(OutcomeStatements),
        [SystemMessage(_LABEL_PROMPT), HumanMessage(format_chunks(source_chunks))],
    )
    confirmed: list[str] = []
    expected: list[str] = []
    for s in labels.statements:
        quote = s.quote.strip()
        if not quote or _normalize(quote) not in source:
            continue  # 원문에 없는 인용은 믿지 않는다(confirmed였어도 버림)
        if s.status == "confirmed":
            confirmed.append(quote)
        elif s.status in ("expected", "planned"):
            expected.append(quote)

    logger.info("outcome_gate: confirmed %d / 기대·계획 %d", len(confirmed), len(expected))
    if not confirmed:
        return None, expected
    written = invoke_with_retry(
        llm.with_structured_output(OutcomeText),
        [SystemMessage(_WRITE_PROMPT), HumanMessage("\n".join(f"- {q}" for q in confirmed))],
    )
    return written.outcome.strip() or None, expected


def append_expected(rationale: str | None, expected: list[str]) -> str | None:
    if not expected:
        return rationale
    tail = "문서가 기대한 효과: " + " / ".join(expected[:MAX_EXPECTED_IN_RATIONALE])
    base = (rationale or "").strip()
    return f"{base}\n{tail}" if base else tail
