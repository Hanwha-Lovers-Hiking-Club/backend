# 기본 RAG 챗봇 테스트.
# 지금은 BM25 키워드 검색만 있다. 검색 그래프와 엔드포인트 테스트는 다음 단계에서 추가한다.
from app.domains.baseline_chat.pipeline.bm25 import BM25Index, tokenize

# ---------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------


def test_tokenize_splits_hangul_into_bigrams_and_keeps_latin_words():
    assert tokenize("결제 API타임아웃") == ["결제", "api", "타임", "임아", "아웃"]


def test_tokenize_keeps_single_hangul_syllable():
    assert tokenize("A 및 B") == ["a", "및", "b"]


def test_tokenize_ignores_punctuation():
    assert tokenize("Next.js, (Supabase)!") == ["next", "js", "supabase"]


# ---------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------


def test_matches_word_with_attached_particle():
    index = BM25Index(["결제모듈에서 장애가 발생했다", "배포 일정을 공유했다"])

    assert [i for i, _ in index.top_k("결제모듈", 5)] == [0]


def test_rare_term_scores_higher_than_common_term():
    index = BM25Index(
        [
            "서버 서버 웹소켓",
            "서버 배포",
            "서버 로그",
            "서버 모니터링",
        ]
    )

    # "웹소켓"은 한 문서에만 있어서 모든 문서에 있는 "서버"보다 점수 기여가 크다
    assert index.top_k("서버 웹소켓", 1)[0][0] == 0


def test_shorter_document_scores_higher_with_same_term_frequency():
    index = BM25Index(
        [
            "캐시 무효화 문제와 함께 다른 여러 가지 긴 설명이 이어지는 문서",
            "캐시 무효화",
        ]
    )

    assert [i for i, _ in index.top_k("캐시", 2)] == [1, 0]


def test_no_matching_token_returns_empty():
    index = BM25Index(["결제 장애", "배포 일정"])

    assert index.top_k("웹소켓", 5) == []


def test_top_k_limits_result_count():
    index = BM25Index(["결제 장애 1", "결제 장애 2", "결제 장애 3"])

    assert len(index.top_k("결제", 2)) == 2


def test_empty_corpus_returns_empty():
    assert BM25Index([]).top_k("결제", 5) == []
