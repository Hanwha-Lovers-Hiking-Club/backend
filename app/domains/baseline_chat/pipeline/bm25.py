# BM25 키워드 검색. 외부 라이브러리 없이 직접 구현한다.
#
# 토큰화: 한국어는 "결제모듈에서", "결제모듈을"처럼 조사가 붙어 공백 단위로 자르면 같은 단어끼리도
# 일치하지 않는다. 그래서 한글 연속 구간은 문자 2-gram("결제", "제모", "모듈", ...)으로 자른다.
# 영문/숫자 연속 구간은 소문자로 바꿔 단어 그대로 쓴다 ("API" -> "api").
#
# 청크 수가 적어서(해커톤 규모) 질문마다 전체 청크로 인덱스를 새로 만들어도 충분히 빠르다.
import math
import re
from collections import Counter

# 한글 음절 연속 구간, 또는 한글이 아닌 글자/숫자 연속 구간
_TOKEN_RE = re.compile(r"[가-힣]+|[^\W_가-힣]+")
_HANGUL_RE = re.compile(r"[가-힣]")

# 일반적으로 쓰는 BM25 기본값
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _TOKEN_RE.findall(text.lower()):
        if _HANGUL_RE.match(word):
            # 한 글자 단어("및", "등")는 2-gram을 만들 수 없어서 그대로 둔다
            if len(word) == 1:
                tokens.append(word)
            else:
                tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


class BM25Index:
    def __init__(self, texts: list[str]):
        self._term_freqs = [Counter(tokenize(text)) for text in texts]
        self._lengths = [sum(freqs.values()) for freqs in self._term_freqs]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0

        doc_freqs: Counter = Counter()
        for freqs in self._term_freqs:
            doc_freqs.update(freqs.keys())
        n = len(texts)
        # 흔한 단어도 점수가 음수가 되지 않도록 1을 더한 형태(Lucene 방식)를 쓴다
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freqs.items()
        }

    def scores(self, query: str) -> list[float]:
        # 질문 안에서 같은 토큰이 반복돼도 한 번만 센다
        terms = [term for term in dict.fromkeys(tokenize(query)) if term in self._idf]
        results: list[float] = []
        for freqs, length in zip(self._term_freqs, self._lengths):
            score = 0.0
            for term in terms:
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                norm = K1 * (1 - B + B * length / self._avg_length)
                score += self._idf[term] * tf * (K1 + 1) / (tf + norm)
            results.append(score)
        return results

    def top_k(self, query: str, k: int) -> list[tuple[int, float]]:
        # (texts에서의 위치, 점수)를 점수 높은 순으로 돌려준다. 일치하는 토큰이 없는 텍스트는 뺀다
        ranked = [(i, score) for i, score in enumerate(self.scores(query)) if score > 0]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return ranked[:k]
