from uuid import uuid4

from app.domains.chat.card_context import format_card


def _document(*excerpts):
    return {
        "document_id": uuid4(),
        "document_title": "문서",
        "original_filename": "문서.pdf",
        "items": [{"id": uuid4(), "location": None, "excerpt": e} for e in excerpts],
    }


def test_format_card_includes_excerpts_of_every_document(make_card):
    card = make_card(
        evidences=[_document("첫 발췌문", "둘째 발췌문"), _document("다른 문서 발췌문")]
    )

    text = format_card(1, card)

    assert "- 첫 발췌문\n- 둘째 발췌문\n- 다른 문서 발췌문" in text


def test_format_card_skips_evidences_without_excerpt(make_card):
    card = make_card(evidences=[_document(None, "남는 발췌문")])

    text = format_card(1, card)

    assert "근거 발췌문:\n- 남는 발췌문" in text


def test_format_card_without_excerpts_has_no_excerpt_section(make_card):
    card = make_card(evidences=[])

    assert "근거 발췌문" not in format_card(1, card)
