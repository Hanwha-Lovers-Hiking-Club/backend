# 프로젝트 폴더에 보여 줄 소개 정보를 업로드당 1번 뽑는다.
# extract와 호출을 나눈 이유: 경험 추출은 프롬프트와 출력 구조까지 비교 실험으로 검증해서,
# 여기에 항목을 더하면 카드 결과가 달라질 수 있다.
# 소개 정보는 없어도 카드는 쓸 수 있으므로, 실패해도 예외를 내지 않고 빈 값을 돌려준다.
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import summarize_error
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import ProjectMetaDraft
from app.domains.documents.pipeline.quotes import plain
from app.domains.documents.pipeline.state import Chunk

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "project_meta.md").read_text(encoding="utf-8")
_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def format_period(start: str | None, end: str | None) -> str | None:
    # 프로젝트 폴더의 기간 표기는 항상 "2026.05 – 2026.07"이다. 종료된 프로젝트만 기간을 둔다
    if not (_MONTH.fullmatch(start or "") and _MONTH.fullmatch(end or "")) or end < start:
        return None
    return f"{start.replace('-', '.')} – {end.replace('-', '.')}"


def _to_fields(draft: ProjectMetaDraft, chunks: dict[str, Chunk]) -> dict[str, str]:
    # 이름과 같은 기준으로, 원문에서 옮기는 값은 원문에 그대로 있을 때만 쓴다.
    # description은 요약문이라 대조할 수 없다
    source = "".join(plain(chunk["text"]) for chunk in chunks.values())

    def in_source(text: str) -> bool:
        target = plain(text)
        return bool(target) and target in source

    team = (draft.team or "").strip()
    quote = draft.period_quote or ""
    fields = {
        "domain": draft.domain,
        "team": team if in_source(team) else None,
        "period": format_period(draft.period_start, draft.period_end) if in_source(quote) else None,
        "description": (draft.description or "").strip(),
    }
    return {name: value for name, value in fields.items() if value}


def describe_project(message: str, chunks: dict[str, Chunk]) -> dict[str, str]:
    # message는 extract가 만든 문서 본문이다. 확인된 항목만 담은 projects 컬럼 값을 돌려준다
    try:
        llm = get_llm().with_structured_output(ProjectMetaDraft)
        draft = invoke_with_retry(llm, [SystemMessage(_PROMPT), HumanMessage(message)])
        return _to_fields(draft, chunks)
    except Exception as e:
        logger.warning("프로젝트 소개 정보 추출 실패, 비워 둠: %s", summarize_error(e))
        return {}
