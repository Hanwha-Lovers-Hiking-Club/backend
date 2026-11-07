import pytest
from pydantic import ValidationError

from app.domains.documents.pipeline import project_meta as PM
from app.domains.documents.pipeline.models import ProjectMetaDraft

CHUNKS = {"D1:0": {"text": "담당: 국제화제품팀\n기간: 2026년 1월 ~ 4월"}}


def _draft(**overrides) -> ProjectMetaDraft:
    values = {
        "domain": "frontend",
        "team": "국제화제품팀",
        "period_quote": "2026년 1월 ~ 4월",
        "period_start": "2026-01",
        "period_end": "2026-04",
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


def _describe(monkeypatch, llm: _LLM) -> dict:
    monkeypatch.setattr(PM, "get_llm", lambda: llm)
    return PM.describe_project("본문", CHUNKS)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-05", "2026-07", "2026.05 – 07"),
        ("2025-12", "2026-02", "2025.12 – 2026.02"),
        ("2026-04", None, "2026.04 –"),
        ("2026-05", "2026-05", "2026.05"),
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
    assert _describe(monkeypatch, _LLM(_draft())) == {
        "domain": "frontend",
        "team": "국제화제품팀",
        "period": "2026.01 – 04",
        "description": "번역 키 누락을 막는 다국어 화면 정비",
    }


def test_team_missing_from_source_is_dropped(monkeypatch):
    assert "team" not in _describe(monkeypatch, _LLM(_draft(team="없는팀")))


def test_period_is_dropped_when_quote_is_not_in_source(monkeypatch):
    assert "period" not in _describe(monkeypatch, _LLM(_draft(period_quote="2025년 내내")))


def test_unknown_fields_are_left_out(monkeypatch):
    draft = _draft(team=None, period_quote=None, description=" ")
    assert _describe(monkeypatch, _LLM(draft)) == {"domain": "frontend"}


def test_domain_is_required_in_llm_output():
    with pytest.raises(ValidationError):
        _draft(domain=None)


def test_llm_failure_leaves_meta_empty(monkeypatch):
    assert _describe(monkeypatch, _LLM(error=RuntimeError("down"))) == {}
