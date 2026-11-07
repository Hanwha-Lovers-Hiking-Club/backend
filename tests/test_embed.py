from app.domains.documents.pipeline.models import (
    CardDraft,
    VerificationResult,
    VerifiedCard,
)
from app.domains.documents.pipeline.nodes import embed as E


def _verified(status: str | None) -> VerifiedCard:
    draft = CardDraft(
        title="t",
        summary="s",
        context="c",
        problem="문제",
        cause=None,
        approach=None,
        rationale=None,
        outcome=None,
        lesson="l",
        technologies=[],
        keywords=[],
        department=None,
        project_date=None,
        evidences=[],
    )
    verdict = "excluded" if status is None else "passed"
    result = VerificationResult(verdict=verdict, reasons=[], status=status)
    return VerifiedCard(draft=draft, verification=result)


def test_only_passed_cards_are_embedded_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(E, "embed_documents", lambda texts: [[0.1]] * len(texts))
    with caplog.at_level("INFO"):
        result = E.embed({"verified_cards": [_verified("draft"), _verified(None)]})
    assert [(c.status, c.embeddings) for c in result["embedded_cards"]] == [("draft", None)]
    assert "draft 카드 1개" in caplog.text


def test_embedding_failure_fails_the_job(monkeypatch):
    def boom(_texts):
        raise RuntimeError("kure down")

    monkeypatch.setattr(E, "embed_documents", boom)
    result = E.embed({"verified_cards": [_verified("active")]})
    assert "카드 임베딩 실패" in result["error"]


def test_failed_card_is_skipped_and_the_rest_continue(monkeypatch):
    calls = {"n": 0}

    def flaky(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("kure down")
        return [[0.1]] * len(texts)

    monkeypatch.setattr(E, "embed_documents", flaky)
    result = E.embed({"verified_cards": [_verified("active"), _verified("active")], "warnings": []})
    assert len(result["embedded_cards"]) == 1 and "error" not in result
    assert "카드 임베딩 실패" in result["warnings"][0]


def test_no_passed_cards_is_a_normal_empty_result(monkeypatch):
    monkeypatch.setattr(E, "embed_documents", lambda texts: [[0.1]] * len(texts))
    assert E.embed({"verified_cards": [_verified(None)]}) == {"embedded_cards": []}
