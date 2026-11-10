from uuid import uuid4

from app.domains.documents.pipeline.models import (
    CardDraft,
    CardEmbeddings,
    CardEvidenceDraft,
    EmbeddedCard,
)
from app.domains.documents.pipeline.nodes import save as S

PROJECT_ID = "project-1"
DOCUMENT_ID = uuid4()


def _state(*cards, **extra) -> dict:
    return {
        "project_id": PROJECT_ID,
        "documents": [{"id": DOCUMENT_ID}],
        "embedded_cards": list(cards),
        **extra,
    }


def _embedded(
    title: str, status="active", document_id=DOCUMENT_ID, technologies=()
) -> EmbeddedCard:
    draft = CardDraft(
        title=title,
        summary="s",
        context="c",
        problem="문제",
        cause=None,
        approach=None,
        rationale=None,
        outcome=None,
        lesson="l",
        technologies=list(technologies),
        keywords=[],
        department=None,
        project_date=None,
        evidences=[CardEvidenceDraft(document_id=document_id, location=None, excerpt=None)],
    )
    vec = [0.1]
    return EmbeddedCard(
        draft=draft,
        embeddings=CardEmbeddings(
            content_embedding=vec,
            situation_embedding=vec,
            solution_embedding=vec,
            lesson_embedding=vec,
        ),
        status=status,
    )


def _patch_create(monkeypatch, fail_titles: set[str]):
    saved: list[str] = []

    def fake(card, _evidences):
        if card["title"] in fail_titles:
            raise RuntimeError("db down")
        saved.append(card["title"])
        return "id"

    monkeypatch.setattr(S.knowledge_cards_service, "create_card", fake)
    return saved


def test_all_saved(monkeypatch):
    saved = _patch_create(monkeypatch, set())
    result = S.save(_state(_embedded("a"), _embedded("b")))
    assert saved == ["a", "b"] and result == {"saved_card_count": 2, "active_card_count": 2}


def test_no_cards_is_a_normal_empty_success(monkeypatch):
    _patch_create(monkeypatch, set())
    assert S.save(_state()) == {"saved_card_count": 0, "active_card_count": 0}


def test_one_failure_keeps_the_rest_and_only_warns(monkeypatch):
    saved = _patch_create(monkeypatch, {"b"})
    result = S.save(_state(_embedded("a"), _embedded("b"), _embedded("c"), warnings=["앞선 경고"]))
    assert saved == ["a", "c"] and result["saved_card_count"] == 2 and "error" not in result
    assert result["warnings"][0] == "앞선 경고" and "'b'" in result["warnings"][1]


def test_all_failed_fails_the_job(monkeypatch):
    _patch_create(monkeypatch, {"a", "b"})
    result = S.save(_state(_embedded("a"), _embedded("b")))
    assert result["saved_card_count"] == 0 and "카드 저장 실패 'a'" in result["error"]


def test_card_is_saved_with_project_of_the_upload(monkeypatch):
    cards: list[dict] = []
    monkeypatch.setattr(
        S.knowledge_cards_service, "create_card", lambda card, _e: cards.append(card) or "id"
    )
    S.save(_state(_embedded("a")))
    assert cards[0]["project_id"] == PROJECT_ID
    assert "project_name" not in cards[0]


def test_card_citing_a_document_outside_the_upload_is_not_saved(monkeypatch):
    saved = _patch_create(monkeypatch, set())
    result = S.save(_state(_embedded("a"), _embedded("밖", document_id=uuid4())))
    assert saved == ["a"] and result["saved_card_count"] == 1
    assert "근거 문서가 프로젝트 밖" in result["warnings"][0]


def test_active_count_excludes_draft_cards(monkeypatch):
    _patch_create(monkeypatch, set())
    result = S.save(_state(_embedded("a"), _embedded("b", status="draft")))
    assert result["saved_card_count"] == 2 and result["active_card_count"] == 1


def test_draft_reasons_are_saved(monkeypatch):
    saved = []
    monkeypatch.setattr(
        S.knowledge_cards_service, "create_card", lambda card, _e: saved.append(card) or "id"
    )
    draft = _embedded("draft", status="draft")
    draft.draft_reasons = ["근거 부족"]
    S.save(_state(draft))
    assert saved[0]["draft_reasons"] == ["근거 부족"]


def test_project_stack_counts_saved_active_cards_only(monkeypatch):
    _patch_create(monkeypatch, {"failed"})

    result = S.save(
        _state(
            _embedded("a", technologies=["postgresql", "somenewtool"]),
            _embedded("b", technologies=["postgresql"]),
            _embedded("draft", status="draft", technologies=["react"]),
            _embedded("failed", technologies=["vue"]),
        )
    )

    assert result["project_stack"] == ["PostgreSQL", "somenewtool"]


def test_project_stack_is_limited(monkeypatch):
    _patch_create(monkeypatch, set())
    technologies = [f"tool{i}" for i in range(S.MAX_PROJECT_STACK + 1)]

    result = S.save(_state(_embedded("a", technologies=technologies)))

    assert len(result["project_stack"]) == S.MAX_PROJECT_STACK
