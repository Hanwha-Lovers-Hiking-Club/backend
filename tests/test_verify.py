from uuid import uuid4

from app.domains.documents.pipeline.models import CardDraft, CardEvidenceDraft, VerificationResult
from app.domains.documents.pipeline.nodes import verify as V


def _draft(**kw) -> CardDraft:
    base = dict(
        title="t",
        summary="s",
        context="c",
        problem="문제",
        cause=None,
        approach=None,
        rationale=" ",
        outcome=None,
        lesson="l",
        technologies=[],
        keywords=[],
        project_name=None,
        department=None,
        project_date=None,
        evidences=[CardEvidenceDraft(document_id=uuid4(), location="1장", excerpt="근거")],
    )
    return CardDraft(**{**base, **kw})


class _FakeLLM:
    def __init__(self):
        self.messages = []

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        return VerificationResult(reason="r", verdict="pass")


def test_prompt_split_null_marker_and_field_order(monkeypatch, caplog):
    fake = _FakeLLM()
    monkeypatch.setattr(V, "get_llm", lambda: fake)
    result = V.verify({"card_drafts": [_draft(), _draft(title=" ")]})

    system, human = fake.messages[0]
    assert system.type == "system" and human.type == "human"
    assert "cause: (없음)" in human.content and "rationale: (없음)" in human.content
    assert "None" not in human.content
    assert len(fake.messages) == 1  # 구조 검사에서 걸린 카드는 LLM을 부르지 않는다
    assert [v.verification.verdict for v in result["verified_cards"]] == ["pass", "discard"]
    assert list(VerificationResult.model_fields) == ["reason", "verdict"]


def test_discard_reason_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(V, "get_llm", lambda: _FakeLLM())
    with caplog.at_level("INFO"):
        V.verify({"card_drafts": [_draft(title=" ")]})
    assert "pass 0 / rewrite 0 / discard 1" in caplog.text and "필수 필드 비어있음" in caplog.text


class _FlakyLLM(_FakeLLM):
    def invoke(self, messages):
        if "title: 나쁜 카드" in messages[1].content:
            raise RuntimeError("llm down")
        return super().invoke(messages)


def test_unverifiable_card_is_skipped_never_passed(monkeypatch):
    monkeypatch.setattr(V, "get_llm", lambda: _FlakyLLM())
    result = V.verify(
        {"card_drafts": [_draft(title="좋은 카드"), _draft(title="나쁜 카드")], "warnings": []}
    )
    assert [v.draft.title for v in result["verified_cards"]] == ["좋은 카드"]
    assert "나쁜 카드" in result["warnings"][0] and "error" not in result


def test_all_llm_verifications_failing_fails_the_job_even_with_structural_discards(monkeypatch):
    monkeypatch.setattr(V, "get_llm", lambda: _FlakyLLM())
    result = V.verify({"card_drafts": [_draft(title=" "), _draft(title="나쁜 카드")]})
    assert "카드 검증 실패" in result["error"]
