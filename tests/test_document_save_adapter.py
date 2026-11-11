import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domains.documents.pipeline.document import DocumentIR
from app.domains.documents.pipeline.document_lifecycle import DocumentSaveError
from app.domains.documents.pipeline.embeddings.models import CardEmbeddings
from app.domains.documents.pipeline.persistence import build_save_payload, save_document
from app.domains.documents.pipeline.processing import DocumentSaveRequest
from app.domains.knowledge_cards import service
from app.domains.knowledge_cards.contracts import CardPersistenceError
from tests.test_document_embedding_texts import make_draft

DOCUMENT_ID = UUID("4c10a229-9b3a-40bc-8611-e2f787540f03")
CARD_ID = UUID("24df586f-90a9-4625-8b3b-caeff907401f")


def save_request(*, card=None, dimensions=1024):
    card = card or make_draft()
    views = ("content", "situation", "solution", "lesson")
    return DocumentSaveRequest(
        document=DocumentIR(
            document_id=DOCUMENT_ID,
            original_filename="original.md",
            blocks=[],
            evidence_index={},
        ),
        cards=[card],
        embeddings=[
            CardEmbeddings(
                experience_id=card.experience_id,
                template_version="card-views-v1",
                model="kure-v3-20261016",
                provider="kure",
                dimensions=dimensions,
                vectors={view: [1.0] * dimensions for view in views},
                input_hashes={view: "a" * 64 for view in views},
            )
        ],
        verification_results=[],
        skips=[],
        payload_hash="b" * 64,
    )


def test_save_adapter_sends_one_atomic_rpc_with_card_evidence_and_four_vectors(monkeypatch):
    class Database:
        params = None

        def rpc(self, name, params):
            assert name == "save_document_cards"
            self.params = params
            return self

        def execute(self):
            return SimpleNamespace(
                data={
                    "document_id": str(DOCUMENT_ID),
                    "payload_hash": "b" * 64,
                    "committed": True,
                    "result_recorded": True,
                    "card_ids": [str(CARD_ID)],
                }
            )

    database = Database()
    monkeypatch.setattr(service, "supabase", database)
    receipt = asyncio.run(save_document(save_request()))
    assert receipt.card_ids == [CARD_ID]
    assert database.params["p_document_id"] == str(DOCUMENT_ID)
    assert database.params["p_payload_hash"] == "b" * 64
    row = database.params["p_cards"][0]
    assert row["status"] == "active"
    assert row["evidences"] == [{"location": "original.md:1", "excerpt": "EXCLUDED_SOURCE_TEXT"}]
    assert all(
        len(row[f"{view}_embedding"]) == 1024
        for view in ("content", "situation", "solution", "lesson")
    )
    assert "source_extraction" not in row and "experience_id" not in row


def test_save_adapter_rejects_dimension_mismatch_before_rpc(monkeypatch):
    class ForbiddenDatabase:
        def rpc(self, *_args):
            raise AssertionError("invalid vectors must not reach the database")

    monkeypatch.setattr(service, "supabase", ForbiddenDatabase())
    with pytest.raises(DocumentSaveError, match="configured model"):
        asyncio.run(save_document(save_request(dimensions=2048)))

    request = save_request()
    request.embeddings[0] = request.embeddings[0].model_copy(
        update={"vectors": {**request.embeddings[0].vectors, "content": [1.0]}}
    )
    with pytest.raises(DocumentSaveError, match="configured model"):
        asyncio.run(save_document(request))


def test_save_adapter_rejects_unconfirmed_receipt(monkeypatch):
    class Database:
        def rpc(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data={"committed": False})

    monkeypatch.setattr(service, "supabase", Database())
    with pytest.raises(DocumentSaveError, match="invalid save receipt"):
        asyncio.run(save_document(save_request()))


class RpcDatabase:
    def __init__(self, *, receipt_update=None, error=None):
        self.calls = []
        self.receipt_update = receipt_update or {}
        self.error = error

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        if self.error:
            raise self.error
        params = self.calls[-1][1]
        return SimpleNamespace(
            data={
                "document_id": params["p_document_id"],
                "payload_hash": params["p_payload_hash"],
                "committed": True,
                "result_recorded": True,
                "card_ids": [str(UUID(int=i + 1)) for i in range(len(params["p_cards"]))],
                **self.receipt_update,
            }
        )


def test_pipeline_adapter_uses_only_public_dict_contract(monkeypatch):
    seen = []

    async def public_service(payload):
        seen.append(payload)
        assert type(payload) is dict
        assert set(payload) == {"document_id", "payload_hash", "cards"}
        assert payload["cards"][0]["evidences"][0]["document_id"] == str(DOCUMENT_ID)
        return {
            "document_id": payload["document_id"],
            "payload_hash": payload["payload_hash"],
            "committed": True,
            "result_recorded": True,
            "card_ids": [str(CARD_ID)],
        }

    monkeypatch.setattr(service, "save_document_cards", public_service)
    receipt = asyncio.run(save_document(save_request()))
    assert receipt.card_ids == [CARD_ID] and len(seen) == 1


@pytest.mark.parametrize("case", ["no_evidence", "wrong_document", "bad_vector", "fake_score"])
def test_public_write_contract_rejects_invalid_payload_before_rpc(monkeypatch, case):
    payload = build_save_payload(save_request())
    card = payload["cards"][0]
    if case == "no_evidence":
        card["evidences"] = []
    elif case == "wrong_document":
        card["evidences"][0]["document_id"] = str(UUID(int=88))
    elif case == "bad_vector":
        card["content_embedding"][0] = True
    else:
        card["quality_score"] = 1.0
    database = RpcDatabase()
    monkeypatch.setattr(service, "supabase", database)
    with pytest.raises(CardPersistenceError, match="Invalid document save request"):
        asyncio.run(service.save_document_cards(payload))
    assert database.calls == []


@pytest.mark.parametrize(
    "update",
    [
        {"document_id": str(UUID(int=88))},
        {"payload_hash": "c" * 64},
        {"card_ids": []},
        {"card_ids": [str(CARD_ID), str(CARD_ID)]},
        {"committed": False},
    ],
)
def test_service_rejects_mismatched_receipt(monkeypatch, update):
    database = RpcDatabase(receipt_update=update)
    monkeypatch.setattr(service, "supabase", database)
    with pytest.raises(DocumentSaveError, match="invalid save receipt"):
        asyncio.run(save_document(save_request()))
    assert len(database.calls) == 1


def test_zero_card_document_still_calls_atomic_service(monkeypatch):
    request = save_request().model_copy(update={"cards": [], "embeddings": []})
    database = RpcDatabase()
    monkeypatch.setattr(service, "supabase", database)
    receipt = asyncio.run(save_document(request))
    assert receipt.card_ids == [] and receipt.committed
    assert database.calls[0][1]["p_cards"] == []


def test_database_error_crosses_adapter_without_fake_success(monkeypatch):
    from postgrest.exceptions import APIError

    database = RpcDatabase(error=APIError({"code": "42883", "message": "RPC is missing"}))
    monkeypatch.setattr(service, "supabase", database)
    with pytest.raises(DocumentSaveError, match="did not confirm"):
        asyncio.run(save_document(save_request()))


@pytest.mark.parametrize("fail", [False, True])
def test_graph_calls_real_service_adapter_and_cleans_only_after_confirmation(
    tmp_path, monkeypatch, fail
):
    from postgrest.exceptions import APIError

    from app.config import settings
    from tests.test_document_processing import Model, graph, inputs, source

    # 실제 모델/DB 대신 의존성만 바꾸고 Parse부터 Save까지 실제 노드 연결을 실행한다.
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    database = RpcDatabase(
        error=APIError({"code": "XX000", "message": "unavailable"}) if fail else None
    )
    monkeypatch.setattr(service, "supabase", database)
    item = source(tmp_path, 1, "Observed a slow query and added an index.")
    state = asyncio.run(graph(Model(), saver=save_document).ainvoke(inputs([item])))
    progress = state["document_progress"][item["document_id"]]
    assert len(database.calls) == 1
    if fail:
        assert progress.status == "incomplete" and progress.saved_card_ids == []
        assert state["documents"] and state["chunk_progress"]
        assert state["processing_failures"][-1].code == "SAVE_NOT_CONFIRMED"
    else:
        assert progress.status == "completed" and progress.saved_card_ids
        assert state["documents"] == [] and state["chunk_progress"] == {}
