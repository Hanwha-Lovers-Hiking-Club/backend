from app.domains.documents.pipeline import outcome_gate as G
from app.domains.documents.pipeline.models import (
    OutcomeStatement,
    OutcomeStatements,
    OutcomeText,
)

CHUNKS = [
    {
        "id": "d:0",
        "document_id": "x",
        "text": "교체 완료(2026-06-25). 장점은 시크릿 중앙화이다. 스키마 반영 필요.",
        "location": "",
    }
]


class _Structured:
    def __init__(self, result, sink):
        self.result, self.sink = result, sink

    def invoke(self, messages):
        self.sink.append(messages[-1].content)
        return self.result


class _LLM:
    def __init__(self, statements, outcome="교체 완료"):
        self.statements, self.outcome, self.sent = statements, outcome, []

    def with_structured_output(self, schema):
        result = (
            OutcomeStatements(statements=self.statements)
            if schema is OutcomeStatements
            else OutcomeText(outcome=self.outcome)
        )
        return _Structured(result, self.sent)


def _s(quote, status):
    return OutcomeStatement(quote=quote, status=status)


def test_only_confirmed_reaches_outcome_and_expected_goes_to_rationale():
    llm = _LLM(
        [
            _s("교체 완료(2026-06-25)", "confirmed"),
            _s("장점은 시크릿 중앙화이다", "expected"),
            _s("스키마 반영 필요", "planned"),
            _s("원문에 없는 문장", "confirmed"),
        ]
    )
    outcome, expected = G.gate_outcome(llm, CHUNKS)
    assert outcome == "교체 완료"
    assert llm.sent[-1] == "- 교체 완료(2026-06-25)"  # 기대·계획·가짜 인용은 쓰기 단계에 안 간다
    assert expected == ["장점은 시크릿 중앙화이다", "스키마 반영 필요"]


def test_no_confirmed_means_no_outcome():
    llm = _LLM([_s("장점은 시크릿 중앙화이다", "expected")])
    assert G.gate_outcome(llm, CHUNKS) == (None, ["장점은 시크릿 중앙화이다"])
    assert len(llm.sent) == 1  # 쓰기 호출은 하지 않는다


def test_append_expected():
    assert G.append_expected("이유", []) == "이유"
    assert G.append_expected(None, ["a", "b"]) == "문서가 기대한 효과: a / b"
    assert G.append_expected("이유", ["a"]) == "이유\n문서가 기대한 효과: a"
    assert G.append_expected(None, list("abcde")).endswith("a / b / c")
