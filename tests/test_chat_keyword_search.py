# BM25 키워드 검색 테스트. 한국어 조사, 정확한 식별자, 순위만 본다.
from app.domains.chat.keyword_search import BM25Index, tokenize


def test_tokenize_splits_hangul_into_bigrams_and_keeps_latin_words():
    assert tokenize("결제모듈 API") == ["결제", "제모", "모듈", "api"]
    assert tokenize("및") == ["및"]


def test_matches_same_word_with_different_josa():
    index = BM25Index(["결제모듈에서 오류가 났다", "회원 이관 일정"])

    assert index.top_k("결제모듈을", 5)[0][0] == 0


def test_exact_identifier_ranks_first():
    index = BM25Index(
        ["모달 내 포커스 문제 2건은 FE-422로 분리했다", "FE 회의록", "포커스 관리 일반론"]
    )

    assert index.top_k("FE-422로 분리된 건 뭐야?", 3)[0][0] == 0


def test_texts_without_any_matching_token_are_excluded():
    index = BM25Index(["웹소켓 갱신", "정산 배치"])

    assert [i for i, _ in index.top_k("웹소켓", 5)] == [0]


def test_empty_index_returns_nothing():
    assert BM25Index([]).top_k("질문", 5) == []
