# 청크 호출 하나를 그래프 실행 한 단계로 처리하고 성공 결과와 제한된 재시도를 보존한다.

import asyncio
import hashlib
import json

from pydantic import ValidationError

from app.domains.documents.pipeline.extraction import experience_id, prompt_text
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.processing import (
    ChunkProgress,
    ProcessingFailure,
    ProcessingOptions,
)


def prepare_extraction(state):
    chunks = {}
    for item in state["chunks"]:
        if item.chunk_id in chunks and chunks[item.chunk_id] != item:
            raise GenerationError("CHUNK_ID_COLLISION", "One chunk ID has different content")
        chunks[item.chunk_id] = item
    documents = {str(item.document_id): item for item in state["documents"]}
    source_hashes = {
        key: hashlib.sha256(item.model_dump_json().encode()).hexdigest()
        for key, item in documents.items()
    }
    records = dict(state.get("chunk_progress", {}))
    keys = []
    for chunk in chunks.values():
        document = documents.get(str(chunk.document_id))
        if document is None:
            raise GenerationError("UNKNOWN_DOCUMENT", "Chunk refers to an unavailable document")
        canonical = {
            "chunk": chunk.model_dump(mode="json"),
            "source_hash": source_hashes[str(chunk.document_id)],
            "prompt": prompt_text("extract"),
            "feedback": [item.model_dump(mode="json") for item in state.get("retry_feedback", [])],
        }
        key = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        keys.append(key)
        if key not in records:
            records[key] = ChunkProgress(
                document_id=chunk.document_id, chunk_id=chunk.chunk_id, fingerprint=key
            )
    return {"chunk_progress": records, "active_chunk_keys": keys}


def transient_failure(exc):
    # 공급자 어댑터가 원래 HTTP 오류를 cause로 보존한다. 인증·잘못된 요청은 반복하지 않는다.
    cause = exc.__cause__
    status = getattr(cause, "status_code", None)
    if status is not None:
        return status in (408, 429) or status >= 500
    return exc.code in {"LLM_API_ERROR", "LLM_TIMEOUT", "LLM_CONNECTION_ERROR", "LLM_RATE_LIMITED"}


def next_chunk(state):
    return (
        "extract"
        if any(
            state["chunk_progress"][key].status == "pending" for key in state["active_chunk_keys"]
        )
        else "merge_extraction"
    )


def make_chunk_worker(node):
    async def worker(state):
        records = dict(state["chunk_progress"])
        record = next(
            (
                records[key]
                for key in state["active_chunk_keys"]
                if records[key].status == "pending"
            ),
            None,
        )
        if record is None:
            return {}
        options = state.get("processing_options", ProcessingOptions())
        if record.attempts:
            await asyncio.sleep(min(30, options.retry_delay_seconds * 2 ** (record.attempts - 1)))
        chunk = next(item for item in state["chunks"] if item.chunk_id == record.chunk_id)
        record = record.model_copy(deep=True)
        record.attempts += 1
        failures = list(state.get("processing_failures", []))
        try:
            update = await node({**state, "chunks": [chunk]})
            record.result = ExtractionResult.model_validate(update["extraction_result"])
            record.status, record.error_code = "succeeded", None
            failures = [
                item
                for item in failures
                if not (
                    item.stage == "extract"
                    and item.document_id == record.document_id
                    and item.chunk_id == record.chunk_id
                )
            ]
        except (GenerationError, ValidationError) as exc:
            record.error_code = (
                exc.code if isinstance(exc, GenerationError) else "LLM_INVALID_OUTPUT"
            )
            retry = isinstance(exc, GenerationError) and transient_failure(exc)
            if not retry or record.attempts >= options.max_chunk_attempts:
                record.status = "failed"
                failures.append(
                    ProcessingFailure(
                        document_id=record.document_id,
                        stage="extract",
                        chunk_id=record.chunk_id,
                        code=record.error_code,
                    )
                )
        records[record.fingerprint] = record
        return {"chunk_progress": records, "processing_failures": failures}

    return worker


def merge_extraction(state):
    experiences = {}
    for key in state["active_chunk_keys"]:
        for item in state["chunk_progress"][key].result.experiences:
            experiences[experience_id(item)] = item
    return {
        "extraction_result": ExtractionResult(experiences=list(experiences.values())),
        "drafts": [],
        "draft_skips": [],
    }
