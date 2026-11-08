import pytest
from pydantic import ValidationError

from app.domains.documents.pipeline import project_meta as PM
from app.domains.documents.pipeline.models import DatedQuote, ProjectMetaDraft

CHUNKS = {
    "D1:0": {"text": "담당: 국제화제품팀\n기간: 2026년 1월 ~ 4월"},
    "D2:0": {"text": "웹파트 주간회의록\n| 일시 | 2026-09-03 |"},
    "D2:1": {"text": "9/1 오픈에서 민원 63건이 접수됐다."},
    "D3:0": {"text": "대기실 분석보고서\n작성일 2026-10-14 / v1.0"},
    "D4:0": {"text": "2025년 회고 메모"},
}


def _date(chunk_id: str, quote: str, month: str, year_quote: str | None = None) -> DatedQuote:
    return DatedQuote(chunk_id=chunk_id, quote=quote, month=month, year_quote=year_quote)


FIRST = _date("D2:0", "| 일시 | 2026-09-03 |", "2026-09")
LAST = _date("D3:0", "작성일 2026-10-14 / v1.0", "2026-10")


def _draft(**overrides) -> ProjectMetaDraft:
    values = {
        "domain": "frontend",
        "team": "국제화제품팀",
        "stated_start": _date("D1:0", "기간: 2026년 1월 ~ 4월", "2026-01"),
        "stated_end": _date("D1:0", "기간: 2026년 1월 ~ 4월", "2026-04"),
        "first_activity": FIRST,
        "last_activity": LAST,
        "description": "번역 키 누락을 막는 다국어 화면 정비",
    }
    return ProjectMetaDraft(**{**values, **overrides})


class _LLM:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        if self.error:
            raise self.error
        return self.result


def _describe(monkeypatch, llm: _LLM) -> tuple[dict, dict]:
    monkeypatch.setattr(PM, "get_llm", lambda: llm)
    return PM.describe_project("본문", CHUNKS)


def _period(monkeypatch, **overrides) -> tuple[str | None, dict]:
    fields, evidence = _describe(monkeypatch, _LLM(_draft(**overrides)))
    return fields.get("period"), evidence


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-05", "2026-07", "2026.05 – 2026.07"),
        ("2025-12", "2026-02", "2025.12 – 2026.02"),
        ("2026-04", None, None),
        ("2026-05", "2026-05", "2026.05 – 2026.05"),
        ("2026-07", "2026-05", None),
        (None, "2026-05", None),
        ("2026-13", None, None),
        ("2026년 5월", None, None),
        ("2026-05", "7월", None),
    ],
)
def test_format_period(start, end, expected):
    assert PM.format_period(start, end) == expected


def test_confirmed_fields_become_project_columns(monkeypatch):
    fields, evidence = _describe(monkeypatch, _LLM(_draft()))
    assert fields == {
        "domain": "frontend",
        "team": "국제화제품팀",
        "period": "2026.01 – 2026.04",
        "description": "번역 키 누락을 막는 다국어 화면 정비",
    }
    assert evidence["kind"] == "stated"


def test_activity_range_is_used_when_no_period_is_stated(monkeypatch):
    period, evidence = _period(monkeypatch, stated_start=None, stated_end=None)
    assert period == "2026.09 – 2026.10"
    assert evidence["kind"] == "activity" and evidence["start"]["chunk_id"] == "D2:0"


def test_a_single_activity_date_gives_the_same_month_on_both_sides(monkeypatch):
    period, _ = _period(monkeypatch, stated_start=None, stated_end=None, last_activity=FIRST)
    assert period == "2026.09 – 2026.09"


def test_a_month_that_the_quote_does_not_say_is_rejected(monkeypatch):
    # 인용은 원문에 있지만, 그 인용은 1월~4월을 말한다. 5월~8월로 답하면 명시 기간을 쓰지 않는다
    period, evidence = _period(
        monkeypatch,
        stated_start=_date("D1:0", "기간: 2026년 1월 ~ 4월", "2026-05"),
        stated_end=_date("D1:0", "기간: 2026년 1월 ~ 4월", "2026-08"),
    )
    assert period == "2026.09 – 2026.10"  # 확인된 활동 기간으로 넘어간다
    assert evidence["rejected"][0]["kind"] == "stated"
    assert "읽을 수 없음" in evidence["rejected"][0]["start"]


def test_one_unconfirmed_boundary_empties_the_period_instead_of_copying(monkeypatch):
    period, evidence = _period(
        monkeypatch,
        stated_start=None,
        stated_end=None,
        last_activity=_date("D3:0", "작성일 2026-11-30", "2026-11"),  # 원문에 없는 구절
    )
    assert period is None
    assert "원문에 없는 구절" in evidence["rejected"][0]["end"]


def test_start_after_end_is_rejected(monkeypatch):
    period, evidence = _period(
        monkeypatch, stated_start=None, stated_end=None, first_activity=LAST, last_activity=FIRST
    )
    assert period is None and "시작이 종료보다 늦음" in evidence["rejected"][0]["range"]


@pytest.mark.parametrize(
    ("year_quote", "ok"),
    [
        ("| 일시 | 2026-09-03 |", True),  # 같은 문서에서 그 날짜와 연결되는 연도
        (None, False),  # 연도 근거 없음
        ("작성일 2026-10-14 / v1.0", False),  # 다른 문서의 연도
        ("2025년 회고 메모", False),  # 원문에 있지만 다른 문서이고 연도도 다르다
    ],
)
def test_a_yearless_date_needs_a_year_from_the_same_document(monkeypatch, year_quote, ok):
    first = _date("D2:1", "9/1 오픈에서 민원 63건이 접수됐다.", "2026-09", year_quote)
    period, _ = _period(monkeypatch, stated_start=None, stated_end=None, first_activity=first)
    assert (period == "2026.09 – 2026.10") is ok


def test_unknown_chunk_is_rejected():
    assert PM.check_date(_date("D9:0", "2026-09-03", "2026-09"), CHUNKS) == "없는 청크: D9:0"


def test_team_missing_from_source_is_dropped(monkeypatch):
    fields, _ = _describe(monkeypatch, _LLM(_draft(team="없는팀")))
    assert "team" not in fields


def test_unknown_fields_are_left_out(monkeypatch):
    draft = _draft(
        team=None,
        stated_start=None,
        stated_end=None,
        first_activity=None,
        last_activity=None,
        description=" ",
    )
    fields, evidence = _describe(monkeypatch, _LLM(draft))
    assert fields == {"domain": "frontend"}
    assert evidence == {"kind": None, "rejected": []}


def test_domain_is_required_in_llm_output():
    with pytest.raises(ValidationError):
        _draft(domain=None)


def test_llm_failure_leaves_meta_empty(monkeypatch):
    assert _describe(monkeypatch, _LLM(error=RuntimeError("down"))) == ({}, {})


def test_a_range_end_without_its_own_year_uses_the_year_in_the_same_quote():
    end = _date("D1:0", "기간: 2026년 1월 ~ 4월", "2026-04")
    assert PM.check_date(end, CHUNKS) is None
    assert PM.check_date(end.model_copy(update={"month": "2025-04"}), CHUNKS) is not None


@pytest.mark.parametrize(
    ("quote", "dated", "yearless"),
    [
        ("작성일 2026-10-14 / v1.0", {(2026, 10)}, set()),
        ("2026년 1월 ~ 4월", {(2026, 1)}, {4}),
        ("9/1 오픈", set(), {9}),
        ("| 07-27~08-02 | 1551 |", set(), {7, 8}),
        ("GT-901 v3.2.1 1-2-3", set(), set()),  # 이슈 번호, 버전, 일련번호는 날짜가 아니다
    ],
)
def test_months_are_read_from_common_date_forms(quote, dated, yearless):
    assert PM._months_in(quote) == (dated, yearless)
