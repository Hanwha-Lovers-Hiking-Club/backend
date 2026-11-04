from app.domains.documents.pipeline.quotes import plain, quote_pieces


def test_plain_keeps_only_letters_and_digits():
    assert plain("확 정, 1,204건 (9/16)") == "확정1204건916"


def test_quote_pieces_split_on_elisions_and_table_cells_and_drop_short_pieces():
    assert quote_pieces("불일치가 나왔다. ... 재계산 후 0건이었다.") == [
        "불일치가나왔다",
        "재계산후0건이었다",
    ]
    assert quote_pieces("| T1 | Pass |") == ["T1Pass"]  # 조각이 모두 짧으면 인용 전체
    assert quote_pieces("   ") == []
