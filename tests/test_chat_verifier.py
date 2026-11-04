# 챗봇 검증 에이전트 테스트. LLM은 가짜로 바꿔 판단 결과를 처리하는 로직만 본다.
import asyncio

import pytest

from app.domains.chat import verifier
from app.domains.chat.models import (
    Candidate,
    CardJudgement,
    ChunkCandidate,
    ChunkJudgement,
    Retrieval,
    VerificationOutput,
)

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


def _output(chunks=(), cards=(), answerable=True, conflicts=None) -> VerificationOutput:
    return VerificationOutput(
        chunk_judgements=list(chunks),
        card_judgements=list(cards),
        answerable=answerable,
        conflicts=conflicts,
    )


def _chunk(n: int) -> ChunkCandidate:
    return ChunkCandidate(
        id=f"chunk-{n}",
        document_id="doc-1",
        document_title=f"문서 {n}",
        chunk_index=n,
        location=f"{n}장",
        content=f"원문 내용 {n}",
        score=0.01,
        similarity=0.4,
    )


@pytest.fixture
def candidates(make_card):
    return [Candidate(card=make_card(f"카드 {i}"), similarity=0.8) for i in range(1, 4)]


@pytest.fixture
def retrieval(candidates):
    return Retrieval(cards=candidates, chunks=[_chunk(1), _chunk(2), _chunk(3)])


def _use(monkeypatch, llm):
    monkeypatch.setattr(verifier, "get_llm", lambda: llm)


async def test_keeps_only_relevant_chunks_and_cards_in_search_order(monkeypatch, retrieval):
    _use(
        monkeypatch,
        FakeLLM(
            _output(
                chunks=[
                    ChunkJudgement(index=3, relevant=True),
                    ChunkJudgement(index=1, relevant=True),
                    ChunkJudgement(index=2, relevant=False),
                ],
                cards=[
                    CardJudgement(index=3, relevant=True, reason="세 번째 이유"),
                    CardJudgement(index=2, relevant=False, reason="무관함"),
                    CardJudgement(index=1, relevant=True, reason="첫 번째 이유"),
                ],
            )
        ),
    )

    result = await verifier.verify("질문", retrieval)

    assert [c.id for c in result.chunks] == ["chunk-1", "chunk-3"]
    assert [(v.candidate.card.title, v.reason) for v in result.cards] == [
        ("카드 1", "첫 번째 이유"),
        ("카드 3", "세 번째 이유"),
    ]
    assert result.answerable is True and result.conflicts is None


async def test_items_without_judgement_are_dropped(monkeypatch, retrieval):
    _use(
        monkeypatch,
        FakeLLM(
            _output(
                chunks=[ChunkJudgement(index=2, relevant=True)],
                cards=[CardJudgement(index=2, relevant=True, reason="이유")],
            )
        ),
    )

    result = await verifier.verify("질문", retrieval)

    assert [c.id for c in result.chunks] == ["chunk-2"]
    assert [v.candidate.card.title for v in result.cards] == ["카드 2"]


async def test_out_of_range_and_duplicate_judgements_are_ignored(monkeypatch, retrieval):
    _use(
        monkeypatch,
        FakeLLM(
            _output(
                chunks=[
                    ChunkJudgement(index=0, relevant=True),
                    ChunkJudgement(index=9, relevant=True),
                    ChunkJudgement(index=1, relevant=False),
                    ChunkJudgement(index=1, relevant=True),
                ],
                cards=[
                    CardJudgement(index=9, relevant=True, reason="범위 밖"),
                    CardJudgement(index=1, relevant=False, reason="무관함"),
                    CardJudgement(index=1, relevant=True, reason="두 번째 판단은 무시"),
                ],
                answerable=False,
            )
        ),
    )

    result = await verifier.verify("질문", retrieval)

    assert result.chunks == [] and result.cards == []


async def test_chunks_linked_to_relevant_cards_are_kept(monkeypatch, retrieval):
    retrieval.cards[0].linked_chunk_ids = ["chunk-3"]
    retrieval.cards[1].linked_chunk_ids = ["chunk-2"]
    _use(
        monkeypatch,
        FakeLLM(
            _output(
                chunks=[ChunkJudgement(index=1, relevant=True)],
                cards=[
                    CardJudgement(index=1, relevant=True, reason="이유"),
                    CardJudgement(index=2, relevant=False, reason="무관함"),
                ],
            )
        ),
    )

    result = await verifier.verify("질문", retrieval)

    # 통과한 카드 1의 연결 원문(chunk-3)은 남고, 탈락한 카드 2의 연결 원문(chunk-2)은 남지 않는다
    assert [c.id for c in result.chunks] == ["chunk-1", "chunk-3"]


async def test_relevant_card_without_reason_is_dropped(monkeypatch, retrieval):
    _use(monkeypatch, FakeLLM(_output(cards=[CardJudgement(index=1, relevant=True, reason="")])))

    assert (await verifier.verify("질문", retrieval)).cards == []


async def test_not_answerable_is_kept_even_with_relevant_items(monkeypatch, retrieval):
    _use(
        monkeypatch,
        FakeLLM(_output(chunks=[ChunkJudgement(index=1, relevant=True)], answerable=False)),
    )

    result = await verifier.verify("질문", retrieval)

    assert result.answerable is False


async def test_answerable_without_any_relevant_item_is_not_trusted(monkeypatch, retrieval):
    _use(monkeypatch, FakeLLM(_output(answerable=True)))

    assert (await verifier.verify("질문", retrieval)).answerable is False


async def test_conflicts_are_passed_through_and_blank_becomes_none(monkeypatch, retrieval):
    chunks = [ChunkJudgement(index=1, relevant=True)]
    _use(monkeypatch, FakeLLM(_output(chunks=chunks, conflicts="v0.9와 v1.0의 결론이 다르다")))
    assert (await verifier.verify("질문", retrieval)).conflicts == "v0.9와 v1.0의 결론이 다르다"

    _use(monkeypatch, FakeLLM(_output(chunks=chunks, conflicts="")))
    assert (await verifier.verify("질문", retrieval)).conflicts is None


async def test_prompt_has_question_numbered_chunks_cards_and_links(monkeypatch, retrieval):
    retrieval.cards[0].linked_chunk_ids = ["chunk-2", "chunk-9"]
    llm = FakeLLM(_output())
    _use(monkeypatch, llm)

    await verifier.verify("RAG 개선 사례?", retrieval)

    assert "RAG 개선 사례?" in llm.prompt
    assert "[원문 1] 문서: 문서 1 · 위치: 1장\n원문 내용 1" in llm.prompt
    assert "[카드 3] 카드 3" in llm.prompt
    # 후보에 없는 청크(chunk-9)는 번호로 바꿀 수 없어서 뺀다
    assert "연결된 원문: 원문 2\n\n[카드 2]" in llm.prompt


async def test_missing_side_is_marked_none(monkeypatch, candidates):
    llm = FakeLLM(_output())
    _use(monkeypatch, llm)

    await verifier.verify("질문", Retrieval(cards=candidates, chunks=[]))

    assert "## 후보 원문\n\n(없음)" in llm.prompt


async def test_llm_failure_keeps_everything_and_says_unverified(monkeypatch, retrieval):
    _use(monkeypatch, FakeLLM(error=RuntimeError("LLM 장애")))

    result = await verifier.verify("질문", retrieval)

    assert result.chunks == retrieval.chunks
    assert [v.candidate for v in result.cards] == retrieval.cards
    assert all(v.reason == verifier.UNVERIFIED_REASON for v in result.cards)
    assert result.answerable is True


async def test_slow_llm_falls_back_like_failure(monkeypatch, retrieval):
    class SlowLLM(FakeLLM):
        async def ainvoke(self, prompt):
            await asyncio.sleep(5)

    _use(monkeypatch, SlowLLM())
    monkeypatch.setattr(verifier, "VERIFY_TIMEOUT_SECONDS", 0.05)

    result = await verifier.verify("질문", retrieval)

    assert result.chunks == retrieval.chunks
    assert all(v.reason == verifier.UNVERIFIED_REASON for v in result.cards)
    assert result.answerable is True


async def test_nothing_found_skips_llm_and_is_not_answerable(monkeypatch):
    def forbidden():
        raise AssertionError("후보가 없으면 LLM을 부르면 안 된다")

    monkeypatch.setattr(verifier, "get_llm", forbidden)

    result = await verifier.verify("질문", Retrieval(cards=[], chunks=[]))

    assert result.chunks == [] and result.cards == [] and result.answerable is False


def test_skip_verification_keeps_everything(retrieval):
    result = verifier.skip_verification(retrieval)

    assert result.chunks == retrieval.chunks
    assert [v.candidate for v in result.cards] == retrieval.cards
    assert result.answerable is True


async def test_verify_candidates_wraps_verify_for_cards_only(monkeypatch, candidates):
    _use(
        monkeypatch,
        FakeLLM(_output(cards=[CardJudgement(index=2, relevant=True, reason="이유")])),
    )

    result = await verifier.verify_candidates("질문", candidates)

    assert [v.candidate.card.title for v in result] == ["카드 2"]
    assert await verifier.verify_candidates("질문", []) == []


def test_unverified_reason_shows_similarity(candidates):
    result = verifier.unverified(candidates)

    assert [v.candidate for v in result] == candidates
    assert "유사도 0.80" in result[0].reason


def test_unverified_reason_for_card_found_only_through_source(make_card):
    [result] = verifier.unverified([Candidate(card=make_card(), similarity=None)])

    assert "원문" in result.reason and "유사도" not in result.reason
