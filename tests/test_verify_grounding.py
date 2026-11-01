# 검증(verify)의 근거 대조: 수치 교차검사, 주장별 인용 판정,
# 코드 규칙이 LLM의 pass를 뒤집는지 테스트.
# 배경: LLM에게 "근거와 일관된가"라고만 물으면 지어낸 수치, 지어낸 원인,
# 반대로 바꾼 결정도 pass였다.
from uuid import uuid4

from app.domains.documents.pipeline.models import CardDraft, CardEvidenceDraft
from app.domains.documents.pipeline.nodes import verify as V

DOC = uuid4()
SOURCE = (
    "ADR: 롤백 런북 3종을 Whitelist에 등록한다. 총 10종이 된다. "
    "월 약 130달러(추정)가 든다. 결정일은 2026-08-13이다. 비용은 1,300원이다."
)


def _draft(**kw) -> CardDraft:
    base = dict(
        title="t",
        summary="s",
        context="c",
        problem="문제",
        cause=None,
        approach="등록한다",
        rationale=None,
        outcome=None,
        lesson="l",
        technologies=[],
        keywords=[],
        project_name=None,
        department=None,
        project_date=None,
        evidences=[CardEvidenceDraft(document_id=DOC, location="결정", excerpt="근거")],
    )
    return CardDraft(**{**base, **kw})


def _chunks(text: str = SOURCE, location: str = "결정") -> list[dict]:
    return [{"document_id": DOC, "index": 0, "text": text, "location": location}]


# --- 수치/날짜 교차검사 ---------------------------------------------------------------------


def test_fabricated_number_is_found():
    draft = _draft(outcome="오차단율이 40% 감소하고 원복이 12초에서 3초로 빨라짐")
    assert V._ungrounded_numbers(draft, SOURCE) == ["12", "40"]


def test_numbers_present_in_source_are_fine_even_with_comma_formatting():
    draft = _draft(summary="총 10종, 비용 1300원(원문 표기 1,300원), 약 130달러")
    assert V._ungrounded_numbers(draft, SOURCE) == []


def test_single_digit_numbers_are_ignored_because_counts_are_often_derived():
    assert V._ungrounded_numbers(_draft(summary="3종과 2가지 대안과 7개"), SOURCE) == []


def test_fabricated_date_is_found_but_source_date_is_not():
    assert V._ungrounded_numbers(_draft(outcome="2026-09-01 배포"), SOURCE)[0] == "2026-09-01"
    assert V._ungrounded_numbers(_draft(context="결정일 2026-08-13"), SOURCE) == []


def test_without_source_text_nothing_is_flagged():
    # 문서 텍스트를 못 구하면(예: 청크가 없는 호출) 모든 수치를 오탐하지 않도록 검사하지 않는다
    assert V._ungrounded_numbers(_draft(outcome="40% 감소"), "") == []


# --- 주장별 인용 판정 -----------------------------------------------------------------------


def _check(**kw) -> V._ClaimCheck:
    base = dict(
        field="approach",
        claim="등록한다",
        quote="롤백 런북 3종을 Whitelist에 등록한다",
        quote_role="결정",
        hedged=False,
    )
    return V._ClaimCheck(**{**base, **kw})


def test_supported_claim_passes():
    assert V._unsupported_claims([_check()], SOURCE) == []


def test_no_quote_or_marked_absent_is_unsupported():
    assert V._unsupported_claims([_check(quote="")], SOURCE)
    assert V._unsupported_claims([_check(quote_role="원문에 없음")], SOURCE)


def test_rejected_alternative_written_as_decision_is_unsupported():
    result = V._unsupported_claims([_check(quote_role="채택하지 않은 대안")], SOURCE)
    assert "채택하지 않은 대안" in result[0]


def test_expectation_written_as_fact_is_unsupported_unless_hedged():
    expectation = _check(field="outcome", quote_role="추정·예상·가정")
    assert V._unsupported_claims([expectation], SOURCE)
    assert (
        V._unsupported_claims(
            [_check(field="outcome", quote_role="추정·예상·가정", hedged=True)], SOURCE
        )
        == []
    )


def test_quote_ignores_punctuation_and_markdown_differences():
    quote = "**롤백 런북 3종을** Whitelist에 — 등록한다."
    assert V._unsupported_claims([_check(quote=quote)], SOURCE) == []


def test_single_unmatched_quote_is_tolerated_but_half_unmatched_is_not():
    # 요약처럼 합성한 주장은 모델이 인용 대신 의역하는 일이 많아서,
    # 한두 건으로 카드를 탈락시키지 않는다
    fake = _check(quote="원문 어디에도 없는 전혀 다른 문장이 여기에 들어 있다")
    assert V._unsupported_claims([_check(), _check(), _check(), fake], SOURCE) == []
    assert V._unsupported_claims([_check(), fake, fake, _check()], SOURCE)


def test_very_long_quote_is_not_existence_checked():
    long_quote = "전혀 다른 문장 " * 100
    assert V._unsupported_claims([_check(quote=long_quote)], SOURCE) == []


# --- verify() 전체: 코드 규칙이 LLM의 pass를 뒤집는다 -----------------------------------------


class _Judge:
    def __init__(self, judgement: V._Judgement):
        self.judgement = judgement
        self.messages = []

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        return self.judgement


def _run(monkeypatch, draft: CardDraft, judgement: V._Judgement, chunks=None):
    judge = _Judge(judgement)
    monkeypatch.setattr(V, "get_llm", lambda: judge)
    state = {"card_drafts": [draft], "chunks": _chunks() if chunks is None else chunks}
    return V.verify(state)["verified_cards"][0].verification, judge


def test_clean_card_passes(monkeypatch):
    verdict, _ = _run(
        monkeypatch, _draft(), V._Judgement(checks=[_check()], reason="ok", verdict="pass")
    )
    assert verdict.verdict == "pass"


def test_llm_pass_is_overridden_when_numbers_are_not_in_source(monkeypatch):
    verdict, judge = _run(
        monkeypatch,
        _draft(outcome="오차단율 40% 감소"),
        V._Judgement(checks=[_check()], reason="괜찮다", verdict="pass"),
    )
    assert verdict.verdict == "rewrite"
    assert "근거 문서에 없는 수치/날짜: 40" in verdict.reason
    assert "## 자동 점검" in judge.messages[0][1].content  # LLM에게도 의심 지점을 알려 준다


def test_llm_pass_is_overridden_when_a_claim_has_no_evidence(monkeypatch):
    absent = _check(field="cause", claim="Redis TTL 오류", quote="", quote_role="원문에 없음")
    verdict, _ = _run(
        monkeypatch,
        _draft(),
        V._Judgement(checks=[_check(), absent], reason="괜찮다", verdict="pass"),
    )
    assert verdict.verdict == "rewrite"
    assert "cause: Redis TTL 오류" in verdict.reason


def test_llm_discard_is_never_softened(monkeypatch):
    verdict, _ = _run(
        monkeypatch, _draft(), V._Judgement(checks=[], reason="무관", verdict="discard")
    )
    assert verdict.verdict == "discard"


def test_source_sections_are_given_to_the_llm_only_for_matching_locations(monkeypatch):
    chunks = _chunks() + [
        {"document_id": DOC, "index": 1, "text": "다른 섹션 본문", "location": "배경"},
    ]
    _, judge = _run(
        monkeypatch, _draft(), V._Judgement(checks=[], reason="ok", verdict="pass"), chunks
    )
    content = judge.messages[0][1].content
    assert "## 원문 섹션" in content and "롤백 런북 3종을" in content
    assert "다른 섹션 본문" not in content


def test_without_chunks_the_card_is_still_judged_by_llm_labels(monkeypatch):
    absent = _check(quote="", quote_role="원문에 없음")
    verdict, judge = _run(
        monkeypatch, _draft(), V._Judgement(checks=[absent], reason="x", verdict="pass"), chunks=[]
    )
    assert verdict.verdict == "rewrite"
    assert "## 원문 섹션" not in judge.messages[0][1].content


def test_lesson_without_a_matching_sentence_is_not_a_problem():
    # lesson은 경험에서 일반화한 시사점이라 원문에 같은 문장이 없는 게 정상이다
    lesson = _check(field="lesson", quote="", quote_role="원문에 없음")
    assert V._unsupported_claims([_check(), lesson], SOURCE) == []
