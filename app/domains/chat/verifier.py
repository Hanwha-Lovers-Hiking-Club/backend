# 3단계: 검색 결과 검증 (멀티에이전트). 답변을 쓰는 LLM 호출(generator.py)과 분리된 별도 호출이고,
# 프롬프트도 따로 쓴다. 이 에이전트는 사용자 질문과 후보 원문·카드만 보며, 생성될 답변은 보지
# 못한다. 그래서 답변 쪽 판단에 끌려가지 않고 자료가 질문에 실제로 맞는지만 교차 검증한다.
#
# 한 번의 호출로 (1) 원문마다 관련 여부 (2) 카드마다 관련 여부와 고른 이유 (3) 자료로 답할 수 있는지
# (4) 원문 간 상충을 판단한다. (3)은 검색 유사도가 아니라 내용을 보고 정한다. 실측에서 자료가 없는
# 질문과 있는 질문의 유사도 차이가 작아서 임계값으로는 가를 수 없었기 때문이다.
#
# 대가: 답변이 나오기까지 LLM 호출이 한 번 더 끼어 지연되고 비용이 늘어난다.
# 끄고 싶으면 환경변수 CHAT_VERIFY_ENABLED=false (config.py).
import asyncio
import logging
from pathlib import Path

from app.domains.chat.card_context import format_card, format_chunks
from app.domains.chat.models import (
    Candidate,
    Retrieval,
    Verification,
    VerificationOutput,
    VerifiedCandidate,
)
from app.domains.documents.pipeline.llm import get_llm

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "verify.md").read_text(encoding="utf-8")

# 검증을 못 했을 때(LLM 실패) 사용자에게 보여줄 "고른 이유"
UNVERIFIED_REASON = "질문과 의미가 가까운 카드로 검색됐습니다. (교차 검증은 진행하지 못했습니다.)"

_NONE = "(없음)"

# 답변 전체를 30초 안에 끝내기 위한 검증 단계 몫 (service.py ANSWER_TIME_LIMIT_SECONDS).
# 실측 3~8초. 넘기면 실패와 똑같이 검증 없이 후보를 그대로 쓴다
VERIFY_TIMEOUT_SECONDS = 12


def _unverified_reason(candidate: Candidate) -> str:
    if candidate.similarity is None:
        return "질문과 관련된 원문에서 나온 카드입니다."
    return f"질문과 의미가 가까운 카드입니다. (유사도 {candidate.similarity:.2f})"


def unverified(candidates: list[Candidate]) -> list[VerifiedCandidate]:
    # 검증을 거치지 않은 후보를 그대로 참조로 쓸 때 사용한다
    return [
        VerifiedCandidate(candidate=candidate, reason=_unverified_reason(candidate))
        for candidate in candidates
    ]


def skip_verification(retrieval: Retrieval) -> Verification:
    # 검증을 끈 경우(CHAT_VERIFY_ENABLED=false). 검색 결과를 모두 관련 있다고 보고 그대로 쓴다
    return Verification(
        chunks=retrieval.chunks,
        cards=unverified(retrieval.cards),
        answerable=bool(retrieval.chunks or retrieval.cards),
    )


def _format_cards(retrieval: Retrieval) -> str:
    # 카드마다 근거가 들어 있는 후보 원문 번호를 붙인다. 원문과 카드를 함께 판단하는 단서가 된다
    chunk_numbers = {chunk.id: number for number, chunk in enumerate(retrieval.chunks, start=1)}
    blocks = []
    for number, candidate in enumerate(retrieval.cards, start=1):
        block = format_card(number, candidate.card)
        linked = [
            f"원문 {chunk_numbers[chunk_id]}"
            for chunk_id in candidate.linked_chunk_ids
            if chunk_id in chunk_numbers
        ]
        if linked:
            block += f"\n연결된 원문: {', '.join(linked)}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _build_prompt(question: str, retrieval: Retrieval) -> str:
    chunks_text = format_chunks(retrieval.chunks) or _NONE
    cards_text = _format_cards(retrieval) or _NONE
    return (
        f"{_PROMPT}## 사용자 질문\n{question}\n\n"
        f"## 후보 원문\n\n{chunks_text}\n\n## 후보 카드\n\n{cards_text}"
    )


def _first_judgements(judgements, count: int) -> dict[int, object]:
    # 번호는 프롬프트에서 1부터 붙였다. 범위를 벗어나거나 같은 항목을 두 번 판단한 건 무시한다
    result: dict[int, object] = {}
    for judgement in judgements:
        if 1 <= judgement.index <= count and judgement.index not in result:
            result[judgement.index] = judgement
    return result


async def verify(question: str, retrieval: Retrieval) -> Verification:
    if not retrieval.chunks and not retrieval.cards:
        return Verification(chunks=[], cards=[], answerable=False)

    try:
        output = await asyncio.wait_for(
            get_llm()
            .with_structured_output(VerificationOutput)
            .ainvoke(_build_prompt(question, retrieval)),
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except Exception:
        # 검증이 실패했다고 답변까지 막지 않는다. 검증 못 했다는 사실은 이유 문구와 로그에 남긴다
        logger.exception("검색 결과 검증 실패, 검증 없이 후보를 그대로 사용")
        return Verification(
            chunks=retrieval.chunks,
            cards=[
                VerifiedCandidate(candidate=candidate, reason=UNVERIFIED_REASON)
                for candidate in retrieval.cards
            ],
            answerable=True,
        )

    # 판단이 빠졌거나 관련 없다고 한 항목은 확인되지 않은 것이라 버린다. 순서는 검색 순위 그대로
    card_judgements = _first_judgements(output.card_judgements, len(retrieval.cards))
    cards = [
        VerifiedCandidate(candidate=candidate, reason=card_judgements[number].reason)
        for number, candidate in enumerate(retrieval.cards, start=1)
        if number in card_judgements
        and card_judgements[number].relevant
        and card_judgements[number].reason
    ]
    # 관련 있다고 판단한 카드에 연결된 원문(그 카드의 근거 문장이 들어 있는 청크)도 관련 원문으로
    # 남긴다. 원문 판단은 같은 입력에서도 흔들렸다(실측: 정답 카드는 3번 모두 통과했는데 남은 원문이
    # 0, 2, 5개). 카드가 맞다면 그 근거가 든 원문도 답변 근거로 맞다
    chunk_judgements = _first_judgements(output.chunk_judgements, len(retrieval.chunks))
    linked = {chunk_id for card in cards for chunk_id in card.candidate.linked_chunk_ids}
    chunks = [
        chunk
        for number, chunk in enumerate(retrieval.chunks, start=1)
        if (number in chunk_judgements and chunk_judgements[number].relevant) or chunk.id in linked
    ]
    # 관련 자료가 하나도 남지 않았는데 답할 수 있다고 한 경우는 믿지 않는다
    answerable = output.answerable and bool(chunks or cards)
    return Verification(
        chunks=chunks, cards=cards, answerable=answerable, conflicts=output.conflicts or None
    )
