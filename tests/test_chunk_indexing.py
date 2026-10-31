from uuid import uuid4

from app.domains.documents.pipeline import graph as G
from app.domains.documents.pipeline.models import EmbeddedChunk
from app.domains.documents.pipeline.nodes import embed_chunks as EC
from app.domains.documents.pipeline.nodes import save_chunks as SC

DOC = uuid4()


def _chunks(n: int, location: str = "1장") -> list[dict]:
    return [
        {
            "id": f"{DOC}:{i}",
            "document_id": DOC,
            "index": i,
            "text": f"본문{i}",
            "location": location,
        }
        for i in range(n)
    ]


def _patch_embed(monkeypatch, fail: bool = False):
    calls: list[list[str]] = []

    def fake(texts):
        calls.append(texts)
        if fail:
            raise RuntimeError("kure down")
        return [[0.1] * 3 for _ in texts]

    monkeypatch.setattr(EC, "embed_documents", fake)
    return calls


def test_embedding_text_has_location_but_content_stays_original(monkeypatch):
    calls = _patch_embed(monkeypatch)
    result = EC.embed_chunks({"chunks": _chunks(1) + _chunks(1, location="")[:0]})
    assert calls == [["1장\n\n본문0"]]
    chunk = result["embedded_chunks"][0]
    assert (chunk.content, chunk.chunk_index, chunk.location) == ("본문0", 0, "1장")


def test_empty_location_is_embedded_as_text_only_and_saved_as_none(monkeypatch):
    calls = _patch_embed(monkeypatch)
    result = EC.embed_chunks({"chunks": _chunks(1, location="")})
    assert calls == [["본문0"]] and result["embedded_chunks"][0].location is None


def test_batches_and_no_chunks_means_no_calls(monkeypatch):
    calls = _patch_embed(monkeypatch)
    result = EC.embed_chunks({"chunks": _chunks(EC.BATCH_SIZE + 1)})
    assert [len(c) for c in calls] == [EC.BATCH_SIZE, 1]
    assert len(result["embedded_chunks"]) == EC.BATCH_SIZE + 1

    calls.clear()
    assert EC.embed_chunks({"chunks": []}) == {"embedded_chunks": []} and calls == []


def test_gives_up_softly_when_embedding_fails(monkeypatch):
    calls = _patch_embed(monkeypatch, fail=True)
    result = EC.embed_chunks({"chunks": _chunks(1), "warnings": ["앞선 경고"]})
    assert len(calls) == 1 and result["embedded_chunks"] == []
    assert "error" not in result and result["warnings"][0] == "앞선 경고"
    assert "청크 임베딩 실패" in result["warnings"][1]


def _embedded(n: int) -> list[EmbeddedChunk]:
    return [
        EmbeddedChunk(
            document_id=DOC, chunk_index=i, location=None, content=f"본문{i}", embedding=[0.1]
        )
        for i in range(n)
    ]


def test_save_inserts_in_batches(monkeypatch):
    inserted: list[int] = []
    monkeypatch.setattr(SC.repository, "insert_chunks", lambda rows: inserted.append(len(rows)))
    result = SC.save_chunks({"embedded_chunks": _embedded(SC.INSERT_BATCH_SIZE + 5)})
    assert result == {} and inserted == [SC.INSERT_BATCH_SIZE, 5]


def test_save_failure_cleans_up_and_only_warns(monkeypatch):
    deleted: list[list[str]] = []
    state = {"n": 0}

    def flaky_insert(rows):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("db down")

    monkeypatch.setattr(SC.repository, "insert_chunks", flaky_insert)
    monkeypatch.setattr(SC.repository, "delete_chunks", lambda ids: deleted.append(ids))
    result = SC.save_chunks({"embedded_chunks": _embedded(SC.INSERT_BATCH_SIZE + 5)})
    assert deleted == [[str(DOC)]]
    assert "error" not in result and "청크 저장 실패" in result["warnings"][0]


def test_save_nothing_when_no_embedded_chunks(monkeypatch):
    monkeypatch.setattr(
        SC.repository, "insert_chunks", lambda rows: (_ for _ in ()).throw(AssertionError)
    )
    assert SC.save_chunks({"embedded_chunks": []}) == {}


def test_chunk_indexing_runs_after_card_save_in_graph():
    assert G._NODE_ORDER[-3:] == ["save", "embed_chunks", "save_chunks"]
    assert G._route_after("save")({"error": None}) == "embed_chunks"
    assert G._route_after("save")({"error": "카드 저장 실패"}) == G.END
