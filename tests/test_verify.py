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


class _SeqLLM:
    # 호출 순서대로 판정을 돌려준다
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return VerificationResult(reason="근거 없는 주장 있음", verdict=self.verdicts.pop(0))


CHUNKS = [{"id": "d:0", "document_id": uuid4(), "text": "근거", "location": "1장"}]


def _redo_setup(monkeypatch, verdicts, *, element=True, redraft=None):
    from app.domains.documents.pipeline.models import ExtractedElements

    monkeypatch.setattr(V, "get_llm", lambda: _SeqLLM(verdicts))
    calls = []
    new = ExtractedElements(
        problem="새 문제",
        cause=None,
        approach=None,
        rationale=None,
        outcome=None,
        source_chunk_ids=["d:0"],
    )

    def fake_re_extract(source_chunks, feedback, original_ids, _llm):
        calls.append(feedback)
        return new if element else None

    monkeypatch.setattr(V, "re_extract_one", fake_re_extract)
    monkeypatch.setattr(
        V,
        "draft_one",
        redraft or (lambda *_a: (_draft(title="새 카드", source_chunk_ids=["d:0"]), 0, False)),
    )
    return calls


def test_rewrite_is_redone_once_and_replaced_when_it_passes(monkeypatch):
    calls = _redo_setup(monkeypatch, ["rewrite", "pass"])
    original = _draft(source_chunk_ids=["d:0"])
    result = V.verify({"card_drafts": [original], "chunks": CHUNKS})
    (card,) = result["verified_cards"]
    assert card.verification.verdict == "pass" and card.draft.title == "새 카드"
    assert calls == ["근거 없는 주장 있음"]  # 검증 지적이 재추출 피드백으로 간다


def test_rewrite_stays_when_redo_fails_or_is_not_possible(monkeypatch):
    # 재시도해도 rewrite면 원래 카드와 판정을 유지하고 더 시도하지 않는다
    calls = _redo_setup(monkeypatch, ["rewrite", "rewrite"])
    original = _draft(source_chunk_ids=["d:0"])
    (card,) = V.verify({"card_drafts": [original], "chunks": CHUNKS})["verified_cards"]
    assert card.verification.verdict == "rewrite" and card.draft is original
    assert len(calls) == 1

    # 재추출 결과가 없으면 유지
    _redo_setup(monkeypatch, ["rewrite"], element=False)
    (card,) = V.verify({"card_drafts": [original], "chunks": CHUNKS})["verified_cards"]
    assert card.draft is original

    # 소스 청크를 모르는 카드(source_chunk_ids 없음)는 재시도 대상이 아니다
    calls = _redo_setup(monkeypatch, ["rewrite"])
    (card,) = V.verify({"card_drafts": [_draft()], "chunks": CHUNKS})["verified_cards"]
    assert card.verification.verdict == "rewrite" and calls == []


def test_redo_exception_keeps_original_verdict_without_failing_the_job(monkeypatch):
    def boom(*_a):
        raise RuntimeError("LLM 오류")

    _redo_setup(monkeypatch, ["rewrite"], redraft=boom)
    original = _draft(source_chunk_ids=["d:0"])
    result = V.verify({"card_drafts": [original], "chunks": CHUNKS})
    assert result["verified_cards"][0].verification.verdict == "rewrite"
    assert "warnings" not in result and "error" not in result
