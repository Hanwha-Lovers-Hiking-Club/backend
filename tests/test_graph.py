import asyncio
from uuid import uuid4

import pytest

from app.domains.documents.pipeline import graph as G
from app.domains.documents.pipeline import retry as R
from app.domains.documents.pipeline.nodes import embed as EM
from app.domains.documents.pipeline.nodes import embed_chunks as EC
from app.domains.documents.pipeline.nodes import parse as P
from app.domains.documents.pipeline.nodes import save as S
from app.domains.documents.pipeline.nodes import save_chunks as SC

TEXT = (
    "# 장애 회고\n\n## 원인\n검색 API가 title 컬럼을 인덱스 없이 LIKE 검색해서 3초로 느려졌다. "
    + "슬로우 쿼리 로그로 확인했다. " * 8
    + "\n\n## 해결\ntitle 컬럼에 인덱스를 추가해 평균 응답 시간이 0.4초로 줄었다. "
    + "배포 전 부하 테스트로 검증했다. " * 8
    + "\n"
)


@pytest.fixture
def env(monkeypatch):
    saved = {"cards": [], "chunk_rows": []}

    async def instant(_seconds):
        pass

    monkeypatch.setattr(R.asyncio, "sleep", instant)
    monkeypatch.setattr(P.repository, "download_file", lambda _path: TEXT.encode())
    monkeypatch.setattr(EM, "embed_documents", lambda texts: [[0.1] * 3] * len(texts))
    monkeypatch.setattr(EC, "embed_documents", lambda texts: [[0.1] * 3] * len(texts))
    monkeypatch.setattr(
        S.knowledge_cards_service, "create_card", lambda card, ev: saved["cards"].append(card)
    )
    monkeypatch.setattr(SC.repository, "insert_chunks", saved["chunk_rows"].extend)

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


def test_without_card_nodes_job_ends_with_zero_cards_and_indexes_chunks(env):
    # 카드 생성 노드가 재설계 중이라 verified_cards가 없다. 카드 0개는 정상이고 청크 인덱싱은 된다
    final = _run()
    assert final.get("error") is None and not final.get("warnings")
    assert final["saved_card_count"] == 0 and env["cards"] == []
    assert env["chunk_rows"] and all(r["embedding"] for r in env["chunk_rows"])


def test_chunk_indexing_failure_only_warns(env, monkeypatch):
    def down(_texts):
        raise RuntimeError("kure down")

    monkeypatch.setattr(EC, "embed_documents", down)
    final = _run()
    assert final.get("error") is None
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
    assert "chunk 단계에서 예기치 못한 오류" in final["error"] and "embedded_chunks" not in final
