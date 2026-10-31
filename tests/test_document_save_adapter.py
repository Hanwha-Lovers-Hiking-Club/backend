import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domains.documents.pipeline.document import DocumentIR
from app.domains.documents.pipeline.document_lifecycle import DocumentSaveError
from app.domains.documents.pipeline.embeddings.models import CardEmbeddings
from app.domains.documents.pipeline.processing import DocumentSaveRequest
from app.domains.knowledge_cards import service
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
    receipt = asyncio.run(service.save_document_cards(save_request()))
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
        asyncio.run(service.save_document_cards(save_request(dimensions=2048)))

    request = save_request()
    request.embeddings[0] = request.embeddings[0].model_copy(
        update={"vectors": {**request.embeddings[0].vectors, "content": [1.0]}}
    )
    with pytest.raises(DocumentSaveError, match="configured model"):
        asyncio.run(service.save_document_cards(request))


def test_save_adapter_rejects_unconfirmed_receipt(monkeypatch):
    class Database:
        def rpc(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data={"committed": False})

    monkeypatch.setattr(service, "supabase", Database())
    with pytest.raises(DocumentSaveError, match="invalid save receipt"):
        asyncio.run(service.save_document_cards(save_request()))
