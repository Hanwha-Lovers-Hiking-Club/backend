# 지식카드와 원문 청크를 LLM 프롬프트에 넣을 텍스트로 바꾼다.
# 검증 에이전트와 답변 생성이 같은 모양을 쓴다.
from app.domains.chat.models import ChunkCandidate
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
        elif field == "outcome":
            # 빈 필드를 그냥 빼면 답변이 효과가 확인된 사례처럼 말할 수 있다
            lines.append("결과: 확인된 기록 없음")
    excerpts = [e.excerpt for document in card.evidences for e in document.items if e.excerpt]
    if excerpts:
        lines.append("근거 발췌문:")
        lines.extend(f"- {excerpt}" for excerpt in excerpts)
    return "\n".join(lines)


def format_cards(cards: list[KnowledgeCard]) -> str:
    # 번호는 1부터. 검증 에이전트가 이 번호로 판단 결과를 돌려준다
    return "\n\n".join(format_card(i, card) for i, card in enumerate(cards, start=1))


def format_chunk(number: int, chunk: ChunkCandidate, with_neighbors: bool = False) -> str:
    header = f"[원문 {number}] 문서: {chunk.document_title}"
    if chunk.location:
        header += f" · 위치: {chunk.location}"
    if not with_neighbors:
        return f"{header}\n{chunk.content}"
    # 답변 생성용. 앞뒤 청크는 맥락이라 근거 본문과 구분해서 넣는다
    parts = [header]
    if chunk.before:
        parts.append(f"(앞 내용) {chunk.before}")
    parts.append(chunk.content)
    if chunk.after:
        parts.append(f"(뒤 내용) {chunk.after}")
    return "\n".join(parts)


def format_chunks(chunks: list[ChunkCandidate], with_neighbors: bool = False) -> str:
    # 번호는 1부터. 검증 에이전트가 이 번호로 판단 결과를 돌려준다
    return "\n\n".join(
        format_chunk(i, chunk, with_neighbors) for i, chunk in enumerate(chunks, start=1)
    )
