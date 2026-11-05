from app.domains.chat.card_context import format_card


def test_card_without_outcome_says_no_confirmed_result(make_card):
    assert "결과: 확인된 기록 없음" in format_card(1, make_card(outcome=None))


def test_card_with_outcome_shows_it(make_card):
    text = format_card(1, make_card(outcome="p95 690ms"))
    assert "결과: p95 690ms" in text and "확인된 기록 없음" not in text
