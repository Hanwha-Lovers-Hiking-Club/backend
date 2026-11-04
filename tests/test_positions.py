from app.domains.documents.pipeline import positions as PS


def _pos(text, location, heading=""):
    return {"text": text, "location": location, "heading": heading}


def test_txt_lines_keep_their_line_numbers():
    positions = PS.text_positions("첫 줄\n\n셋째 줄 내용", markdown=False)
    assert [(p["text"], p["location"]) for p in positions] == [
        ("첫 줄", "1행"),
        ("셋째 줄 내용", "3행"),
    ]


def test_md_lines_get_the_same_heading_path_as_chunks():
    text = "# 회고\n## 원인\n경쟁 조건이다.\n```\n# 코드 주석\n```\n## 해결\n잠금을 걸었다."
    by_text = {p["text"]: p for p in PS.text_positions(text, markdown=True)}
    assert by_text["경쟁 조건이다."]["location"] == "회고 · 원인 · 3행"
    assert by_text["# 코드 주석"]["heading"] == "회고 · 원인"  # 코드블록 안의 #는 헤딩이 아니다
    assert by_text["잠금을 걸었다."]["location"] == "회고 · 해결 · 8행"


def test_excel_column_names():
    assert [PS._column(i) for i in (0, 25, 26, 27)] == ["A", "Z", "AA", "AB"]


def test_locate_finds_the_item_with_the_quote_ignoring_spacing_and_symbols():
    positions = [
        _pos("불일치 1,204건이 나왔다.", "p.1 · 2.1 발견"),
        _pos("9/16 재계산 후 0건", "p.2 · 2.2 조사"),
    ]
    assert PS.locate("불일치 1204건이 나왔다", positions) == "p.1 · 2.1 발견"
    assert PS.locate("9/16 재계산 후 0건", positions) == "p.2 · 2.2 조사"


def test_locate_spanning_items_returns_a_range():
    positions = [
        _pos("| T1 | 통과 20회 |", "시트 시험 · A2:B2"),
        _pos("| T2 | 실패 2회 남음 |", "시트 시험 · A3:B3"),
    ]
    assert (
        PS.locate("T1 통과 20회 ... T2 실패 2회 남음", positions)
        == "시트 시험 · A2:B2 ~ 시트 시험 · A3:B3"
    )
    # 인용 조각 하나가 두 항목 경계에 걸쳐도 이어 붙여 찾는다
    split = [_pos("한글 입력 시험은", "1행"), _pos("모두 통과했다", "2행")]
    assert PS.locate("한글 입력 시험은 모두 통과했다", split) == "1행 ~ 2행"


def test_repeated_text_prefers_the_heading_of_the_evidence_chunk():
    positions = [
        _pos("재시험 결과 Pass 확인", "p.1 · 1. 개요", "1. 개요"),
        _pos("재시험 결과 Pass 확인", "p.3 · 3. 결과", "3. 결과"),
    ]
    assert PS.locate("재시험 결과 Pass 확인", positions, heading="3. 결과") == "p.3 · 3. 결과"
    assert PS.locate("재시험 결과 Pass 확인", positions) == "p.1 · 1. 개요"


def test_quote_not_in_the_document_is_not_located():
    assert PS.locate("원문에 없는 문장입니다", [_pos("다른 내용만 있다", "1행")]) is None
    assert PS.locate("아무거나", []) is None


def test_whole_quote_is_matched_before_pieces_that_also_appear_elsewhere():
    # 날짜 조각만 보면 앞 시트에서도 잡히지만, 인용 전체(여러 행)는 뒤 시트에만 있다
    positions = [
        _pos("| M-1 | 2026-09-07 | 반환 |", "시트 회수 · A6:C6"),
        _pos("| 2026-09-07 | v3.8.0 | 중복 0 |", "시트 점검 · A5:C5"),
        _pos("| 2026-09-14 | v3.8.0 | 중복 0 |", "시트 점검 · A6:C6"),
    ]
    quote = "| 2026-09-07 | v3.8.0 | 중복 0 | | 2026-09-14 | v3.8.0 | 중복 0 |"
    assert PS.locate(quote, positions) == "시트 점검 · A5:C5 ~ 시트 점검 · A6:C6"
