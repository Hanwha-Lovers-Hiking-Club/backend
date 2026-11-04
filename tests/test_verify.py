from uuid import uuid4

from app.domains.documents.pipeline.models import (
    CardDraft,
    CardEvidenceDraft,
    ClaimCheck,
    ClaimChecks,
    Evidence,
    Experience,
)
from app.domains.documents.pipeline.nodes import verify as V

DOC = uuid4()
CHUNKS = {
    0: "입력 보존 회귀 시험\n프로젝트: 케어링크",
    1: "Chrome에서 한글 입력 시험 20회 모두 통과했다.",
    2: "Safari에서는 20회 중 2회 입력이 유실됐다.",
}
QUOTES = {
    "chrome": (1, "Chrome에서 한글 입력 시험 20회 모두 통과했다."),
    "safari": (2, "Safari에서는 20회 중 2회 입력이 유실됐다."),
}


def _cid(i: int) -> str:
    return f"{DOC}:{i}"


def _experience() -> Experience:
    return Experience(
        title="한글 입력 유실",
        summary="s",
        link_hints=[],
        evidences=[
            Evidence(
                kind="action", statement="조합 이벤트 처리", chunk_id=_cid(1), quote="Chrome에서"
            ),
            Evidence(
                kind="result", statement="Chrome 통과", chunk_id=_cid(1), quote=QUOTES["chrome"][1]
            ),
            Evidence(
                kind="result", statement="Safari 실패", chunk_id=_cid(2), quote=QUOTES["safari"][1]
            ),
        ],
    )


def _draft(title="한글 입력 유실 해결", outcome="Chrome 시험 20회 통과", **overrides) -> CardDraft:
    evidences = [
        CardEvidenceDraft(
            document_id=DOC, location=None, excerpt="Chrome에서", chunk_id=_cid(1), kind="action"
        ),
        CardEvidenceDraft(
            document_id=DOC,
            location=None,
            excerpt=QUOTES["chrome"][1],
            chunk_id=_cid(1),
            kind="result",
        ),
        CardEvidenceDraft(
            document_id=DOC, location=None, excerpt="프로젝트: 케어링크", chunk_id=_cid(0)
        ),
    ]
    fields = dict(
        title=title,
        summary="요약",
        context="배경",
        problem=None,
        cause=None,
        approach="조합 이벤트 처리",
        rationale=None,
        outcome=outcome,
        lesson="교훈",
        technologies=["chrome"],
        keywords=[],
        project_name="케어링크",
        department=None,
        project_date=None,
        evidences=evidences,
        field_sources={
            "title": [1],
            "summary": [1],
            "context": [0],
            "approach": [0],
            "outcome": [1],
            "lesson": [0, 1],
            "project_name": [2],
        },
        experience_index=0,
    )
    return CardDraft(**{**fields, **overrides})


def _state(*drafts) -> dict:
    return {
        "documents": [
            {
                "id": DOC,
                "storage_bucket": "b",
                "storage_path": "p",
                "original_filename": "시험.xlsx",
            }
        ],
        "chunks": [
            {"id": _cid(i), "document_id": DOC, "index": i, "text": t, "location": ""}
            for i, t in CHUNKS.items()
        ],
        "experiences": [_experience()],
        "card_drafts": list(drafts),
    }


def _checks(draft: CardDraft, **bad) -> ClaimChecks:
    # 값이 있는 모든 필드를 supported(lesson은 lesson_within_scope)로 두고,
    # bad에 준 필드만 그 판정으로 바꾼다
    checks = []
    for field in V._FIELDS:
        if V._value(draft, field):
            ok = "lesson_within_scope" if field == "lesson" else "supported"
            checks.append(
                ClaimCheck(
                    field=field,
                    claim=f"{field} 주장",
                    evidence_ids=["E1"],
                    note="n",
                    judgement=bad.get(field, ok),
                )
            )
    return ClaimChecks(checks=checks)


class _LLM:
    def __init__(self, results):
        self.results, self.messages = results, []

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages.append(messages[1].content)
        title = messages[1].content.split("title: ")[1].split("  [")[0]
        result = self.results[title]
        if isinstance(result, Exception):
            raise result
        return result


def _run(monkeypatch, results, *drafts) -> tuple[dict, _LLM]:
    llm = _LLM(results)
    monkeypatch.setattr(V, "get_llm", lambda: llm)
    return V.verify(_state(*drafts)), llm


def test_verifier_sees_all_evidence_of_the_experience_not_only_the_cited_ones(monkeypatch):
    draft = _draft(summary="Safari 포함 30회 시험")
    _, llm = _run(monkeypatch, {draft.title: _checks(draft)}, draft)
    message = llm.messages[0]
    assert "title: 한글 입력 유실 해결  [근거: E2]" in message
    assert (
        "Safari 실패" in message and QUOTES["safari"][1] in message
    )  # 카드가 인용하지 않은 근거와 청크
    assert "H1 [문서 머리말]" in message and "project_name: 케어링크  [근거: H1]" in message
    assert "같은 문자열로 찾지 못했다" in message and ": 30" in message  # 수치 신호(자동 탈락 아님)


def test_all_supported_with_outcome_is_passed_and_active(monkeypatch):
    draft = _draft()
    result, _ = _run(monkeypatch, {draft.title: _checks(draft)}, draft)
    [card] = result["verified_cards"]
    assert (card.verification.verdict, card.verification.status) == ("passed", "active")


def test_decision_card_without_outcome_passes_but_is_published_as_draft(monkeypatch):
    draft = _draft(outcome=None, field_sources={**_draft().field_sources, "outcome": []})
    result, _ = _run(monkeypatch, {draft.title: _checks(draft)}, draft)
    v = result["verified_cards"][0].verification
    assert (v.verdict, v.status) == ("passed", "draft") and "결과(outcome) 없음" in v.reasons[0]


def test_bad_conditional_field_is_cleared_and_unused_evidence_is_dropped(monkeypatch):
    draft = _draft(title="조합 이벤트 처리", summary="요약", lesson="교훈")
    checks = _checks(draft, outcome="overstated", technologies="not_in_evidence")
    result, _ = _run(monkeypatch, {draft.title: checks}, draft)
    card = result["verified_cards"][0]
    assert card.verification.cleared_fields == ["outcome", "technologies"]
    assert card.draft.outcome is None and card.draft.technologies == []
    assert (card.verification.verdict, card.verification.status) == ("passed", "draft")
    # 남은 필드가 인용하는 근거만 남고 번호가 다시 매겨진다
    for field, idx in card.draft.field_sources.items():
        assert all(i < len(card.draft.evidences) for i in idx), field


def test_overclaim_in_a_required_field_holds_the_card(monkeypatch):
    draft = _draft()
    result, _ = _run(
        monkeypatch,
        {draft.title: _checks(draft, title="overstated", lesson="lesson_overreach")},
        draft,
    )
    v = result["verified_cards"][0].verification
    assert (v.verdict, v.status) == ("held", "draft") and len(v.reasons) == 2


def test_clearing_the_only_conditional_field_excludes_the_card(monkeypatch):
    draft = _draft(approach=None, field_sources={**_draft().field_sources, "approach": []})
    result, _ = _run(monkeypatch, {draft.title: _checks(draft, outcome="not_a_result")}, draft)
    v = result["verified_cards"][0].verification
    assert (v.verdict, v.status) == ("excluded", None)


def test_missing_judgement_or_failed_call_is_incomplete_not_held(monkeypatch):
    missing, failed, ok = _draft(title="a"), _draft(title="b"), _draft(title="c")
    checks = ClaimChecks(checks=[c for c in _checks(missing).checks if c.field != "summary"])
    results = {"a": checks, "b": RuntimeError("terra down"), "c": _checks(ok)}
    result, _ = _run(monkeypatch, results, missing, failed, ok)
    verdicts = [(c.verification.verdict, c.verification.status) for c in result["verified_cards"]]
    assert verdicts == [("incomplete", None), ("incomplete", None), ("passed", "active")]
    assert "판정 누락: summary" in result["warnings"][0]
    assert "검증 호출 실패" in result["warnings"][1]


def test_all_cards_incomplete_fails_the_job(monkeypatch):
    result, _ = _run(monkeypatch, {"b": RuntimeError("terra down")}, _draft(title="b"))
    assert result["error"].startswith("카드 검증 미완료")


def test_no_drafts_skips_the_llm(monkeypatch):
    monkeypatch.setattr(V, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("호출 금지")))
    assert V.verify(_state()) == {"verified_cards": []}


def test_unconfirmed_result_keeps_the_outcome_but_is_published_as_draft(monkeypatch):
    # 원문이 효과 판단을 보류한 관찰은 사실이라 outcome을 남기지만 공개(active)하지 않는다
    draft = _draft()
    result, _ = _run(
        monkeypatch, {draft.title: _checks(draft, outcome="result_unconfirmed")}, draft
    )
    card = result["verified_cards"][0]
    assert (card.verification.verdict, card.verification.status) == ("passed", "draft")
    assert card.draft.outcome and card.verification.cleared_fields == []
    assert "결과 미확정" in card.verification.reasons[0]
