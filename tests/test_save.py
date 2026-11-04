from app.domains.documents.pipeline.models import CardDraft, CardEmbeddings, EmbeddedCard
from app.domains.documents.pipeline.nodes import save as S


def _embedded(title: str) -> EmbeddedCard:
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
        technologies=[],
        keywords=[],
        project_name=None,
        department=None,
        project_date=None,
        evidences=[],
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
    result = S.save({"embedded_cards": [_embedded("a"), _embedded("b")]})
    assert saved == ["a", "b"] and result == {"saved_card_count": 2}


def test_no_cards_is_a_normal_empty_success(monkeypatch):
    _patch_create(monkeypatch, set())
    assert S.save({"embedded_cards": []}) == {"saved_card_count": 0}


def test_one_failure_keeps_the_rest_and_only_warns(monkeypatch):
    saved = _patch_create(monkeypatch, {"b"})
    result = S.save(
        {
            "embedded_cards": [_embedded("a"), _embedded("b"), _embedded("c")],
            "warnings": ["앞선 경고"],
        }
    )
    assert saved == ["a", "c"] and result["saved_card_count"] == 2 and "error" not in result
    assert result["warnings"][0] == "앞선 경고" and "'b'" in result["warnings"][1]


def test_all_failed_fails_the_job(monkeypatch):
    _patch_create(monkeypatch, {"a", "b"})
    result = S.save({"embedded_cards": [_embedded("a"), _embedded("b")]})
    assert result["saved_card_count"] == 0 and "카드 저장 실패 'a'" in result["error"]
