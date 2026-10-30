# 문서별 임베딩·저장 결과를 구분하고 영속 저장이 확인된 문서의 임시 상태를 정리한다.

import hashlib
import json
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.domains.documents.pipeline.embeddings.models import EmbeddingError
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.processing import (
    DocumentProgress,
    DocumentSaveRequest,
    ProcessingFailure,
    SaveReceipt,
)


class DocumentSaver(Protocol):
    # 실제 구현은 knowledge_cards.service를 거치며 payload_hash로 같은 요청을 재사용한다.
    async def __call__(self, request: DocumentSaveRequest) -> SaveReceipt: ...


class DocumentSaveError(RuntimeError):
    pass


def belongs(item, document_id):
    return any(
        ref.document_id == document_id for ref in item.source_extraction.evidence_index.values()
    )


def clear_document_work(state, document_id):
    # 원본 파일과 최종 카드·근거는 삭제하지 않는다. 현재 State의 임시 복사본만 정리한다.
    ids = {
        item.experience_id
        for name in ("drafts", "settled_drafts", "draft_skips", "settled_skips")
        for item in state.get(name, [])
        if belongs(item, document_id)
    }
    update = {}
    for name in (
        "parsed_documents",
        "all_parsed_documents",
        "documents",
        "all_documents",
        "chunks",
        "all_chunks",
    ):
        update[name] = [item for item in state.get(name, []) if item.document_id != document_id]
    for name in (
        "drafts",
        "settled_drafts",
        "draft_skips",
        "settled_skips",
        "verified_drafts",
        "review_drafts",
        "verification_results",
        "settled_results",
        "embedding_texts",
        "embeddings",
    ):
        update[name] = [item for item in state.get(name, []) if item.experience_id not in ids]
    update["extraction_result"] = ExtractionResult(
        experiences=[
            item
            for item in state.get("extraction_result", ExtractionResult()).experiences
            if not any(ref.document_id == document_id for ref in item.evidence_index.values())
        ]
    )
    records = {
        key: item
        for key, item in state.get("chunk_progress", {}).items()
        if item.document_id != document_id
    }
    update["chunk_progress"] = records
    update["active_chunk_keys"] = [
        key for key in state.get("active_chunk_keys", []) if key in records
    ]
    return update


def next_document(state):
    return (
        "embed"
        if any(
            str(item.document_id) not in state["document_progress"] for item in state["all_sources"]
        )
        else "end"
    )


def document_scope(state, document_id):
    scope = {**state}
    ids = {
        item.experience_id
        for name in ("drafts", "draft_skips")
        for item in state.get(name, [])
        if belongs(item, document_id)
    }
    for name in (
        "drafts",
        "draft_skips",
        "verified_drafts",
        "review_drafts",
        "verification_results",
        "embedding_texts",
        "embeddings",
    ):
        scope[name] = [item for item in state.get(name, []) if item.experience_id in ids]
    for name in ("sources", "all_sources", "parsed_documents", "documents", "chunks"):
        scope[name] = [item for item in state.get(name, []) if item.document_id == document_id]
    return scope


def save_payload_hash(scope):
    # experience_id만으로 멱등성을 판단하지 않는다. 원본·카드·벡터가 같은 저장 요청인지 구분한다.
    payload = {
        name: [item.model_dump(mode="json") for item in scope.get(name, [])]
        for name in (
            "documents",
            "verified_drafts",
            "embeddings",
            "verification_results",
            "draft_skips",
        )
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def make_document_embedder(embed_node):
    async def worker(state):
        source = next(
            (
                item
                for item in state["all_sources"]
                if str(item.document_id) not in state["document_progress"]
            ),
            None,
        )
        if source is None:
            return {"active_document_id": None}
        document_id = source.document_id
        update = {"active_document_id": str(document_id)}
        scope = document_scope(state, document_id)
        if (
            any(item.document_id == document_id for item in state["processing_failures"])
            or scope["review_drafts"]
        ):
            return update
        try:
            embedded = await embed_node(scope)
            update["embeddings"] = [*state.get("embeddings", []), *embedded["embeddings"]]
            update["embedding_usage"] = [
                *state.get("embedding_usage", []),
                *embedded["embedding_usage"],
            ]
        except EmbeddingError as exc:
            update["processing_failures"] = [
                *state["processing_failures"],
                ProcessingFailure(
                    document_id=document_id,
                    stage="embed",
                    code=exc.code,
                ),
            ]
        return update

    return worker


def make_document_saver(saver: DocumentSaver | None = None):
    async def worker(state):
        update = {"document_save_receipt": None}
        if saver is None or state.get("active_document_id") is None:
            return update
        document_id = UUID(state["active_document_id"])
        scope = document_scope(state, document_id)
        if (
            any(item.document_id == document_id for item in state["processing_failures"])
            or scope["review_drafts"]
        ):
            return update
        try:
            expected = {
                item.experience_id: set(item.texts)
                for item in scope["embedding_texts"]
                if item.texts
            }
            actual = {item.experience_id: set(item.vectors) for item in scope["embeddings"]}
            if expected != actual or len(scope["documents"]) != 1:
                raise DocumentSaveError("Required document or embeddings are missing")
            request = DocumentSaveRequest(
                document=scope["documents"][0],
                cards=scope["verified_drafts"],
                embeddings=scope["embeddings"],
                verification_results=scope["verification_results"],
                skips=scope["draft_skips"],
                payload_hash=save_payload_hash(scope),
            )
            receipt = SaveReceipt.model_validate(await saver(request))
            if (
                receipt.document_id != document_id
                or receipt.payload_hash != request.payload_hash
                or len(receipt.card_ids) != len(request.cards)
                or len(set(receipt.card_ids)) != len(receipt.card_ids)
            ):
                raise DocumentSaveError("Save receipt does not match this document")
            update["document_save_receipt"] = receipt
        except (DocumentSaveError, ValidationError):
            update["processing_failures"] = [
                *state["processing_failures"],
                ProcessingFailure(
                    document_id=document_id,
                    stage="save",
                    code="SAVE_NOT_CONFIRMED",
                ),
            ]
        return update

    return worker


def make_document_completer():
    async def worker(state):
        if state.get("active_document_id") is None:
            return {}
        document_id = UUID(state["active_document_id"])
        records = [
            item for item in state["chunk_progress"].values() if item.document_id == document_id
        ]
        latest = {item.chunk_id: item for item in records}
        failures = list(state["processing_failures"])
        local_failures = [item for item in failures if item.document_id == document_id]
        scope = document_scope(state, document_id)
        progress = DocumentProgress(
            document_id=document_id,
            status="ready_to_save",
            successful_chunks=sum(item.status == "succeeded" for item in latest.values()),
            failed_chunks=sum(item.status == "failed" for item in latest.values()),
            admitted_cards=len(scope["verified_drafts"]),
            review_cards=len(scope["review_drafts"]),
            rejected_cards=sum(item.decision == "reject" for item in scope["verification_results"]),
            skipped_experiences=len(scope["draft_skips"]),
        )
        update = {}
        if local_failures:
            progress.status = (
                "failed" if any(item.stage == "parse" for item in local_failures) else "incomplete"
            )
        elif scope["review_drafts"]:
            progress.status = "needs_review"
        else:
            progress.payload_hash = save_payload_hash(scope)
            receipt = state.get("document_save_receipt")
            if receipt is not None:
                if (
                    receipt.document_id != document_id
                    or receipt.payload_hash != progress.payload_hash
                ):
                    raise DocumentSaveError("Save receipt does not match the current state")
                progress.status = "completed"
                progress.saved_card_ids = receipt.card_ids
                update.update(clear_document_work(state, document_id))
        return {
            **update,
            "active_document_id": None,
            "document_save_receipt": None,
            "processing_failures": failures,
            "document_progress": {**state["document_progress"], str(document_id): progress},
        }

    return worker


async def release_completed_checkpoints(
    state, checkpointer, thread_id, *, job_result_recorded=False
):
    # 마지막 체크포인트를 쓰는 그래프 실행이 끝난 뒤 상위 서비스에서 호출한다.
    # 문서 하나만 완료됐을 때 작업 전체 thread를 지우면 다른 문서의 복구 자료가 사라진다.
    progress = state.get("document_progress", {})
    if (
        not job_result_recorded
        or not progress
        or set(progress) != {str(item.document_id) for item in state["all_sources"]}
        or any(item.status != "completed" for item in progress.values())
    ):
        raise ValueError("Persist the final job result and complete every document before cleanup")
    await checkpointer.adelete_thread(thread_id)
