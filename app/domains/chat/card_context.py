# 지식카드를 LLM 프롬프트에 넣을 텍스트로 바꾼다. 검증 에이전트와 답변 생성이 같은 모양을 쓴다.
from app.domains.knowledge_cards.schemas import KnowledgeCard

_FIELDS = (
    ("요약", "summary"),
    ("배경", "context"),
    ("문제", "problem"),
    ("원인", "cause"),
    ("해결 방법", "approach"),
    ("선택 이유", "rationale"),
    ("결과", "outcome"),
    ("교훈", "lesson"),
)


def format_card(number: int, card: KnowledgeCard) -> str:
    lines = [f"[카드 {number}] {card.title}"]
    if card.project_name:
        lines.append(f"프로젝트: {card.project_name}")
    if card.technologies:
        lines.append(f"기술 스택: {', '.join(card.technologies)}")
    for label, field in _FIELDS:
        value = getattr(card, field)
        if value:
            lines.append(f"{label}: {value}")
    excerpts = [e.excerpt for e in card.evidences if e.excerpt]
    if excerpts:
        lines.append("근거 발췌문:")
        lines.extend(f"- {excerpt}" for excerpt in excerpts)
    return "\n".join(lines)


def format_cards(cards: list[KnowledgeCard]) -> str:
    # 번호는 1부터. 검증 에이전트가 이 번호로 판단 결과를 돌려준다
    return "\n\n".join(format_card(i, card) for i, card in enumerate(cards, start=1))
