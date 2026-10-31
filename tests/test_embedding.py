from types import SimpleNamespace

import pytest

from app.domains.documents.pipeline import embedding as E


def _response(*vectors):
    # 일부러 순서를 섞어 index 기준 정렬을 확인한다
    data = [SimpleNamespace(index=i, embedding=v) for i, v in enumerate(vectors)]
    return SimpleNamespace(data=list(reversed(data)))


def _patch_client(monkeypatch, response):
    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_kw: response))
    monkeypatch.setattr(E, "get_embedding_client", lambda: client)


def test_vectors_are_returned_in_input_order(monkeypatch):
    first, second = [0.1] * E.EMBEDDING_DIMENSIONS, [0.2] * E.EMBEDDING_DIMENSIONS
    _patch_client(monkeypatch, _response(first, second))
    assert E.embed_documents(["a", "b"]) == [first, second]


def test_count_mismatch_fails_with_clear_message(monkeypatch):
    _patch_client(monkeypatch, _response([0.1] * E.EMBEDDING_DIMENSIONS))
    with pytest.raises(ValueError, match="개수 불일치"):
        E.embed_documents(["a", "b"])


def test_dimension_mismatch_fails_with_clear_message(monkeypatch):
    _patch_client(monkeypatch, _response([0.1] * 3))
    with pytest.raises(ValueError, match="차원 불일치"):
        E.embed_documents(["a"])


def test_client_retries_transient_errors_itself(monkeypatch):
    monkeypatch.setattr(E.settings, "KURE_API_KEY", "test")
    E.get_embedding_client.cache_clear()
    assert E.get_embedding_client().max_retries == E.EMBEDDING_MAX_RETRIES == 2
    E.get_embedding_client.cache_clear()
