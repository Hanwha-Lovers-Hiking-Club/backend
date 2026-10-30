# 3단계: 검색한 카드 검증 (멀티에이전트). 답변을 쓰는 LLM 호출(generator.py)과 분리된 별도 호출이고,
# 프롬프트도 따로 쓴다. 이 에이전트는 사용자 질문과 후보 카드만 보며, 생성될 답변은 보지 못한다.
# 그래서 답변 쪽 판단에 끌려가지 않고 카드가 질문에 실제로 맞는지만 교차 검증한다.
#
# 대가: 답변이 나오기까지 LLM 호출이 한 번 더 끼어 지연되고 비용이 늘어난다.
# 끄고 싶으면 환경변수 CHAT_VERIFY_ENABLED=false (config.py).
import logging
from pathlib import Path

from app.domains.chat.card_context import format_cards
from app.domains.chat.models import Candidate, VerificationOutput, VerifiedCandidate
from app.domains.documents.pipeline.llm import get_llm

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "verify.md").read_text(encoding="utf-8")

# 검증을 못 했을 때(LLM 실패) 사용자에게 보여줄 "고른 이유"
UNVERIFIED_REASON = "질문과 의미가 가까운 카드로 검색됐습니다. (교차 검증은 진행하지 못했습니다.)"


def unverified(candidates: list[Candidate]) -> list[VerifiedCandidate]:
    # 검증을 거치지 않은 후보를 그대로 참조로 쓸 때 사용한다
    return [
        VerifiedCandidate(
            candidate=candidate,
            reason=f"질문과 의미가 가까운 카드입니다. (유사도 {candidate.similarity:.2f})",
        )
        for candidate in candidates
    ]


async def verify_candidates(question: str, candidates: list[Candidate]) -> list[VerifiedCandidate]:
    if not candidates:
        return []

    cards_text = format_cards([candidate.card for candidate in candidates])
    prompt = f"{_PROMPT}## 사용자 질문\n{question}\n\n## 후보 카드\n\n{cards_text}"

    try:
        output = await get_llm().with_structured_output(VerificationOutput).ainvoke(prompt)
    except Exception:
        # 검증이 실패했다고 답변까지 막지 않는다. 검증 못 했다는 사실은 이유 문구와 로그에 남긴다
        logger.exception("지식카드 검증 실패, 검증 없이 후보를 그대로 사용")
        return [
            VerifiedCandidate(candidate=candidate, reason=UNVERIFIED_REASON)
            for candidate in candidates
        ]

    # 번호는 프롬프트에서 1부터 붙였다. 범위를 벗어나거나 같은 카드를 두 번 판단한 건 무시한다
    reasons: dict[int, str] = {}
    for judgement in output.judgements:
        if 1 <= judgement.index <= len(candidates) and judgement.index not in reasons:
            reasons[judgement.index] = judgement.reason if judgement.relevant else ""

    # 판단이 빠졌거나 관련 없다고 한 카드는 확인되지 않은 것이라 버린다. 순서는 검색 순위 그대로
    return [
        VerifiedCandidate(candidate=candidate, reason=reasons[number])
        for number, candidate in enumerate(candidates, start=1)
        if reasons.get(number)
    ]
