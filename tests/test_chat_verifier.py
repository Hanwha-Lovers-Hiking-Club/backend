# 챗봇 검증 에이전트 테스트. LLM은 가짜로 바꿔 판단 결과를 처리하는 로직만 본다.
import pytest

from app.domains.chat import verifier
from app.domains.chat.models import Candidate, CardJudgement, VerificationOutput

pytestmark = pytest.mark.anyio


class FakeLLM:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.prompt = None

    def with_structured_output(self, schema):
        assert schema is VerificationOutput
        return self

    async def ainvoke(self, prompt):
        self.prompt = prompt
        if self.error:
            raise self.error
        return self.output


@pytest.fixture
def candidates(make_card):
    return [Candidate(card=make_card(f"카드 {i}"), similarity=0.8) for i in range(1, 4)]


def _use(monkeypatch, llm):
    monkeypatch.setattr(verifier, "get_llm", lambda: llm)


async def test_keeps_only_relevant_cards_in_search_order(monkeypatch, candidates):
    _use(
        monkeypatch,
        FakeLLM(
            VerificationOutput(
                judgements=[
                    CardJudgement(index=3, relevant=True, reason="세 번째 이유"),
                    CardJudgement(index=2, relevant=False, reason="무관함"),
                    CardJudgement(index=1, relevant=True, reason="첫 번째 이유"),
                ]
            )
        ),
    )

    result = await verifier.verify_candidates("질문", candidates)

    assert [(v.candidate.card.title, v.reason) for v in result] == [
        ("카드 1", "첫 번째 이유"),
        ("카드 3", "세 번째 이유"),
    ]


async def test_cards_without_judgement_are_dropped(monkeypatch, candidates):
    _use(
        monkeypatch,
        FakeLLM(
            VerificationOutput(judgements=[CardJudgement(index=2, relevant=True, reason="이유")])
        ),
    )

    result = await verifier.verify_candidates("질문", candidates)

    assert [v.candidate.card.title for v in result] == ["카드 2"]


async def test_out_of_range_and_duplicate_judgements_are_ignored(monkeypatch, candidates):
    _use(
        monkeypatch,
        FakeLLM(
            VerificationOutput(
                judgements=[
                    CardJudgement(index=0, relevant=True, reason="범위 밖"),
                    CardJudgement(index=9, relevant=True, reason="범위 밖"),
                    CardJudgement(index=1, relevant=False, reason="무관함"),
                    CardJudgement(index=1, relevant=True, reason="두 번째 판단은 무시"),
                ]
            )
        ),
    )

    assert await verifier.verify_candidates("질문", candidates) == []


async def test_prompt_has_question_and_numbered_cards(monkeypatch, candidates):
    llm = FakeLLM(VerificationOutput(judgements=[]))
    _use(monkeypatch, llm)

    await verifier.verify_candidates("RAG 개선 사례?", candidates)

    assert "RAG 개선 사례?" in llm.prompt
    assert "[카드 1] 카드 1" in llm.prompt
    assert "[카드 3] 카드 3" in llm.prompt


async def test_llm_failure_keeps_candidates_and_says_unverified(monkeypatch, candidates):
    _use(monkeypatch, FakeLLM(error=RuntimeError("LLM 장애")))

    result = await verifier.verify_candidates("질문", candidates)

    assert [v.candidate for v in result] == candidates
    assert all(v.reason == verifier.UNVERIFIED_REASON for v in result)


async def test_no_candidates_skips_llm(monkeypatch):
    def forbidden():
        raise AssertionError("후보가 없으면 LLM을 부르면 안 된다")

    monkeypatch.setattr(verifier, "get_llm", forbidden)

    assert await verifier.verify_candidates("질문", []) == []


def test_unverified_reason_shows_similarity(candidates):
    result = verifier.unverified(candidates)

    assert [v.candidate for v in result] == candidates
    assert "유사도 0.80" in result[0].reason
