import asyncio
import re
from uuid import uuid4

import pytest

from app.domains.documents.pipeline import graph as G
from app.domains.documents.pipeline import retry as R
from app.domains.documents.pipeline.models import (
    CardDraftContent,
    CardExcerptPick,
    ExtractedElements,
    ExtractedElementsBatch,
    VerificationResult,
)
from app.domains.documents.pipeline.nodes import draft_card as D
from app.domains.documents.pipeline.nodes import embed as EM
from app.domains.documents.pipeline.nodes import embed_chunks as EC
from app.domains.documents.pipeline.nodes import extract as EX
from app.domains.documents.pipeline.nodes import parse as P
from app.domains.documents.pipeline.nodes import save as S
from app.domains.documents.pipeline.nodes import save_chunks as SC
from app.domains.documents.pipeline.nodes import verify as V

TEXT = (
    "# 장애 회고\n\n## 원인\n검색 API가 title 컬럼을 인덱스 없이 LIKE 검색해서 3초로 느려졌다. "
    + "슬로우 쿼리 로그로 확인했다. " * 8
    + "\n\n## 해결\ntitle 컬럼에 인덱스를 추가해 평균 응답 시간이 0.4초로 줄었다. "
    + "배포 전 부하 테스트로 검증했다. " * 8
    + "\n"
)


class _Bound:
    def __init__(self, schema, experiences, fail_problem):
        self.schema, self.experiences, self.fail_problem = schema, experiences, fail_problem

    def invoke(self, messages):
        user = messages[1].content
        if self.schema is ExtractedElementsBatch:
            ids = re.findall(r'<chunk id="([^"]+)"', user)
            return ExtractedElementsBatch(
                elements=[
                    ExtractedElements(
                        problem=f"문제{i}",
                        cause=None,
                        approach="해결",
                        rationale=None,
                        outcome=None,
                        source_chunk_ids=[ids[0]],
                    )
                    for i in range(self.experiences)
                ]
            )
        if self.schema is CardDraftContent:
            if self.fail_problem and f"problem: {self.fail_problem}" in user:
                raise RuntimeError("llm down")
            chunk_id = re.findall(r'<chunk id="([^"]+)"', user)[0]
            return CardDraftContent(
                title="t",
                summary="s",
                context="c",
                lesson="l",
                technologies=[],
                keywords=[],
                project_name=None,
                department=None,
                project_date=None,
                excerpts=[CardExcerptPick(chunk_id=chunk_id, excerpt="")],
            )
        return VerificationResult(reason="r", verdict="pass")


class _LLM:
    def __init__(self, experiences=2, fail_problem=None):
        self.experiences, self.fail_problem = experiences, fail_problem

    def with_structured_output(self, schema):
        return _Bound(schema, self.experiences, self.fail_problem)


@pytest.fixture
def env(monkeypatch):
    saved = {"cards": [], "chunk_rows": []}

    async def instant(_seconds):
        pass

    monkeypatch.setattr(R.asyncio, "sleep", instant)
    monkeypatch.setattr(P.repository, "download_file", lambda _path: TEXT.encode())
    llm = _LLM()
    for module in (EX, D, V):
        monkeypatch.setattr(module, "get_llm", lambda: llm)
    monkeypatch.setattr(EM, "embed_documents", lambda texts: [[0.1] * 3] * len(texts))
    monkeypatch.setattr(EC, "embed_documents", lambda texts: [[0.1] * 3] * len(texts))
    monkeypatch.setattr(
        S.knowledge_cards_service, "create_card", lambda card, ev: saved["cards"].append(card)
    )
    monkeypatch.setattr(SC.repository, "insert_chunks", saved["chunk_rows"].extend)

    def set_llm(**kw):
        llm = _LLM(**kw)
        for module in (EX, D, V):
            monkeypatch.setattr(module, "get_llm", lambda: llm)

    saved["llm"] = set_llm
    return saved


def _run() -> dict:
    state = {
        "job_id": uuid4(),
        "documents": [
            {
                "id": uuid4(),
                "storage_bucket": "b",
                "storage_path": "p",
                "original_filename": "회고.txt",
            }
        ],
    }
    return asyncio.run(G.build_graph().ainvoke(state))


def test_happy_path_saves_cards_and_indexes_chunks(env):
    final = _run()
    assert final.get("error") is None and not final.get("warnings")
    assert final["saved_card_count"] == 2 and len(env["cards"]) == 2
    assert env["chunk_rows"] and all(r["embedding"] for r in env["chunk_rows"])
    assert {c["status"] for c in env["cards"]} == {"active"}


def test_one_failed_experience_is_skipped_and_the_rest_are_saved(env):
    env["llm"](experiences=2, fail_problem="문제0")
    final = _run()
    assert final.get("error") is None and final["saved_card_count"] == 1
    assert len(final["warnings"]) == 1 and "문제0" in final["warnings"][0]
    assert env["chunk_rows"]  # 카드 일부가 실패해도 청크 인덱싱은 계속된다


def test_no_experiences_is_a_normal_result_and_chunks_are_still_indexed(env):
    env["llm"](experiences=0)
    final = _run()
    assert final.get("error") is None and final["saved_card_count"] == 0
    assert env["cards"] == [] and env["chunk_rows"]


def test_chunk_indexing_failure_only_warns(env, monkeypatch):
    def down(_texts):
        raise RuntimeError("kure down")

    monkeypatch.setattr(EC, "embed_documents", down)
    final = _run()
    assert final.get("error") is None and final["saved_card_count"] == 2
    assert "청크 임베딩 실패" in final["warnings"][0] and env["chunk_rows"] == []


def test_parse_failure_stops_the_job_after_retries(env, monkeypatch):
    calls = {"n": 0}

    def down(_path):
        calls["n"] += 1
        raise RuntimeError("storage down")

    monkeypatch.setattr(P.repository, "download_file", down)
    final = _run()
    assert calls["n"] == 3 and "회고.txt" in final["error"] and "chunks" not in final
    assert env["cards"] == [] and env["chunk_rows"] == []


def test_unexpected_exception_in_a_node_becomes_an_error_instead_of_raising(env, monkeypatch):
    def boom(_state):
        raise ZeroDivisionError("버그")

    monkeypatch.setitem(G._NODES, "chunk", boom)
    final = _run()
    assert "chunk 단계에서 예기치 못한 오류" in final["error"] and "extracted_elements" not in final
