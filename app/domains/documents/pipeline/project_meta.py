# 프로젝트 폴더에 보여 줄 소개 정보를 업로드당 1번 뽑는다.
# extract와 호출을 나눈 이유: 경험 추출은 프롬프트와 출력 구조까지 비교 실험으로 검증해서,
# 여기에 항목을 더하면 카드 결과가 달라질 수 있다.
# 소개 정보는 없어도 카드는 쓸 수 있으므로, 실패해도 예외를 내지 않고 빈 값을 돌려준다.
#
# 기간(period)은 "문서에서 확인되는 활동 기간"이다. 문서에 프로젝트 기간이 명시돼 있으면 그것을,
# 없으면 문서에 기록된 이 프로젝트의 첫 활동 날짜와 마지막 활동 날짜를 쓴다. 진행 중인
# 프로젝트도 활동 기록이 있으면 채운다(마지막 활동 월은 종료 월이 아니다).
# LLM이 고른 날짜는 그대로 믿지 않는다. 경계마다 원문 구절을 받아, 구절이 원문에 있고 그 구절에서
# 그 연월을 실제로 읽을 수 있을 때만 쓴다. 한쪽 경계만 확인되면 다른 쪽을 복사해 채우지 않고
# 비운다(확인되지 않은 범위가 정상 값처럼 저장되는 것을 막는다).
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import summarize_error
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import DatedQuote, ProjectMetaDraft
from app.domains.documents.pipeline.quotes import plain
from app.domains.documents.pipeline.state import Chunk

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "project_meta.md").read_text(encoding="utf-8")
_MONTH = re.compile(r"(20\d{2})-(0[1-9]|1[0-2])")
# 연도가 있는 날짜: 2026-09-03, 2026.09, 2026/9, 2026년 9월
_DATED = re.compile(r"(20\d{2})\s*(?:[-./]|년)\s*(\d{1,2})(?!\d)")
# 연도가 없는 날짜: 9/1, 10월, 07-27(표의 기간 칸 "07-27~08-02" 등)
_YEARLESS = re.compile(
    r"(?<![\d.])(\d{1,2})\s*(?:/\s*\d{1,2}|월)|(?<![\w.-])(\d{1,2})-\d{1,2}(?![\d.-])"
)
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def format_period(start: str | None, end: str | None) -> str | None:
    # 프로젝트 폴더의 기간 표기는 항상 "2026.05 – 2026.07"이다. 같은 달이어도 줄이지 않는다
    if not (_MONTH.fullmatch(start or "") and _MONTH.fullmatch(end or "")) or end < start:
        return None
    return f"{start.replace('-', '.')} – {end.replace('-', '.')}"


def _months_in(quote: str) -> tuple[set[tuple[int, int]], set[int]]:
    # (연도가 있는 (연, 월) 집합, 연도가 없는 월 집합)
    dated = {(int(y), int(m)) for y, m in _DATED.findall(quote) if 1 <= int(m) <= 12}
    rest = _DATED.sub(" ", quote)
    months = (a or b for a, b in _YEARLESS.findall(rest))
    yearless = {int(m) for m in months if 1 <= int(m) <= 12}
    return dated, yearless


def _document_text(chunks: dict[str, Chunk], chunk_id: str) -> str:
    doc = chunk_id.split(":")[0]
    return "".join(plain(c["text"]) for alias, c in chunks.items() if alias.split(":")[0] == doc)


def check_date(date: DatedQuote | None, chunks: dict[str, Chunk]) -> str | None:
    """경계 날짜를 원문과 대조한다. 확인되면 None, 아니면 버린 이유를 돌려준다."""
    if date is None:
        return "없음"
    match = _MONTH.fullmatch(date.month.strip())
    if not match:
        return f"연월 형식이 아님: {date.month!r}"
    year, month = int(match.group(1)), int(match.group(2))
    chunk = chunks.get(date.chunk_id)
    if chunk is None:
        return f"없는 청크: {date.chunk_id}"
    quote = plain(date.quote)
    if not quote or quote not in plain(chunk["text"]):
        return f"원문에 없는 구절: {date.quote!r}"
    dated, yearless = _months_in(date.quote)
    if (year, month) in dated:
        return None
    if month not in yearless:
        return f"구절에서 {date.month}을(를) 읽을 수 없음: {date.quote!r}"
    # "2026년 1월 ~ 4월"의 4월처럼 같은 구절 안에 연도가 있으면 그 연도로 본다
    if year in {int(y) for y in _YEAR.findall(date.quote)}:
        return None
    # 그 밖의 연도 없는 날짜는 같은 문서에서 그 날짜와 연결되는 연도 근거가 있어야 한다
    year_quote = date.year_quote or ""
    if not plain(year_quote) or plain(year_quote) not in _document_text(chunks, date.chunk_id):
        return f"연도 근거가 같은 문서에 없음: {year_quote!r}"
    if year not in {int(y) for y in _YEAR.findall(year_quote)}:
        return f"연도 근거에 {year}년이 없음: {year_quote!r}"
    return None


def _period(draft: ProjectMetaDraft, chunks: dict[str, Chunk]) -> tuple[str | None, dict]:
    # (기간, 근거 기록). 명시된 기간을 먼저 보고, 두 경계가 모두 확인될 때만 쓴다
    evidence: dict = {"kind": None, "rejected": []}
    for kind, start, end in (
        ("stated", draft.stated_start, draft.stated_end),
        ("activity", draft.first_activity, draft.last_activity),
    ):
        if start is None and end is None:
            continue
        problems = {side: check_date(d, chunks) for side, d in (("start", start), ("end", end))}
        failed = {side: reason for side, reason in problems.items() if reason}
        period = format_period(start.month, end.month) if not failed else None
        if period:
            evidence.update(kind=kind, start=start.model_dump(), end=end.model_dump())
            return period, evidence
        if not failed:
            failed = {"range": f"시작이 종료보다 늦음: {start.month} > {end.month}"}
        evidence["rejected"].append({"kind": kind, **failed})
    return None, evidence


def _to_fields(draft: ProjectMetaDraft, chunks: dict[str, Chunk]) -> tuple[dict[str, str], dict]:
    # 이름과 같은 기준으로, 원문에서 옮기는 값은 원문에 그대로 있을 때만 쓴다.
    # description은 요약문이라 대조할 수 없다
    source = "".join(plain(chunk["text"]) for chunk in chunks.values())

    def in_source(text: str) -> bool:
        target = plain(text)
        return bool(target) and target in source

    team = (draft.team or "").strip()
    period, evidence = _period(draft, chunks)
    fields = {
        "domain": draft.domain,
        "team": team if in_source(team) else None,
        "period": period,
        "description": (draft.description or "").strip(),
    }
    return {name: value for name, value in fields.items() if value}, evidence


def describe_project(message: str, chunks: dict[str, Chunk]) -> tuple[dict[str, str], dict]:
    """(확인된 항목만 담은 projects 컬럼 값, 기간 근거 기록)을 돌려준다.

    message는 extract가 만든 문서 본문이고 chunks는 그 별칭("D1:3") -> 청크다.
    근거 기록은 DB에 저장하지 않는다(dry-run 리포트와 로그용).
    """
    try:
        llm = get_llm().with_structured_output(ProjectMetaDraft)
        draft = invoke_with_retry(llm, [SystemMessage(_PROMPT), HumanMessage(message)])
        fields, evidence = _to_fields(draft, chunks)
    except Exception as e:
        logger.warning("프로젝트 소개 정보 추출 실패, 비워 둠: %s", summarize_error(e))
        return {}, {}
    if evidence["rejected"]:
        logger.info("프로젝트 기간 근거 제외: %s", evidence["rejected"])
    return fields, evidence
