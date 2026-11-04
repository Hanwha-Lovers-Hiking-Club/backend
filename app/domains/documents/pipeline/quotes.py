# 인용문을 원문과 비교하는 규칙. extract의 인용 검사, positions의 위치 찾기, draft_card의 선택 필드
# 대조가 모두 이 규칙을 쓴다(한쪽만 바뀌면 "인용은 통과했는데 위치는 못 찾는" 식으로 어긋난다).
import re

# 비교는 글자와 숫자만 본다. PDF 변환문은 줄바꿈 자리에 공백이 끼고("대 사"),
# 모델은 기호를 빼고 인용하는 일이 흔하다
_NON_TEXT = re.compile(r"[\W_]+")
# 모델은 인용 중간을 "..."로 줄이거나 떨어진 문장·표 칸을 이어 붙인다. 조각마다 원문에 있는지 본다
_QUOTE_SPLIT = re.compile(r"\.\.\.|…|\[\.\.\.\]|(?<=[.다음함됨임])\s+|\n|\s\|\s")
# 이보다 짧은 조각(표의 "Pass", 숫자 하나 등)은 어디에나 있을 수 있어서 따로 보지 않는다
MIN_PIECE_CHARS = 6


def plain(text: str) -> str:
    return _NON_TEXT.sub("", text)


def quote_pieces(quote: str) -> list[str]:
    """인용문을 원문과 대조할 조각들로 나눈다. 조각이 모두 짧으면 인용 전체를 한 조각으로 쓴다."""
    pieces = [p for p in map(plain, _QUOTE_SPLIT.split(quote)) if len(p) >= MIN_PIECE_CHARS]
    whole = plain(quote)
    return pieces or ([whole] if whole else [])
