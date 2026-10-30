"""Graph execution bookkeeping; no LLM/DB clients and no graph assembly."""

from typing import Literal

from app.domains.documents.pipeline.document import ChunkOptions, DocumentSource, ParseOptions
from app.domains.documents.pipeline.extraction import experience_id
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.verification.models import RetryWork, VerificationResult
from app.domains.documents.pipeline.verification.retry import TARGET_ORDER
from app.domains.documents.pipeline.verification.structure_checker import issue


def initialize(state):
    sources = [DocumentSource.model_validate(item) for item in state.get("sources", [])]
    if len({source.document_id for source in sources}) != len(sources):
        raise ValueError("sources must have unique document IDs")
    return {
        "sources": sources,
        "all_sources": sources,
        "parse_options": ParseOptions.model_validate(state.get("parse_options", ParseOptions())),
        "chunk_options": ChunkOptions.model_validate(state.get("chunk_options", ChunkOptions())),
        "all_parsed_documents": [],
        "all_documents": [],
        "all_chunks": [],
        "settled_results": [],
        "settled_drafts": [],
        "settled_skips": [],
        "pending_retries": [],
        "active_retry": None,
        "retry_route": "settle",
        "verification_history": [],
        "retry_feedback": [],
        "retry_requests": [],
        "verification_results": [],
        "verified_drafts": [],
        "review_drafts": [],
        "extraction_result": ExtractionResult(),
        "drafts": [],
        "draft_skips": [],
        "parsed_documents": [],
        "documents": [],
        "chunks": [],
    }


def merge_documents(old, new):
    return list({item.document_id: item for item in [*old, *new]}.values())


def select_candidates(state):
    # A chunk retry can rediscover another already settled/pending experience.
    excluded = {item.experience_id for item in state.get("settled_results", [])}
    excluded.update(item.draft.experience_id for item in state.get("pending_retries", []))
    return {
        "extraction_result": ExtractionResult(
            experiences=[
                item
                for item in state["extraction_result"].experiences
                if experience_id(item) not in excluded
            ]
        )
    }


def settle(state):
    results = {item.experience_id: item for item in state.get("settled_results", [])}
    drafts = {item.experience_id: item for item in state.get("settled_drafts", [])}
    current = {item.experience_id: item for item in state.get("drafts", [])}
    queue = list(state.get("pending_retries", []))
    old_docs = {item.document_id: item for item in state.get("all_documents", [])}
    changed = {
        item.document_id
        for item in state.get("documents", [])
        if item.document_id in old_docs and old_docs[item.document_id] != item
    }
    for key, draft in drafts.items():
        if changed.intersection(
            ref.document_id for ref in draft.source_extraction.evidence_index.values()
        ):
            results[key] = VerificationResult(
                experience_id=key,
                decision="needs_review",
                issues=[
                    issue(
                        "evidence",
                        "SOURCE_CHANGED_DURING_RETRY",
                        "source_extraction",
                        "Source changed after an earlier decision",
                        disposition="needs_review",
                        action="Revalidate this experience against the changed document",
                    )
                ],
            )
    for result in state.get("verification_results", []):
        if result.decision != "retry":
            results[result.experience_id] = result
            if result.experience_id in current:
                drafts[result.experience_id] = current[result.experience_id].model_copy(deep=True)
    for request in state.get("retry_requests", []):
        draft = current.get(request.experience_id)
        if draft is not None:
            queue.append(RetryWork(request=request, draft=draft.model_copy(deep=True)))
        else:
            results[request.experience_id] = VerificationResult(
                experience_id=request.experience_id,
                decision="needs_review",
                issues=[
                    issue(
                        "retry",
                        "RETRY_CONTEXT_MISSING",
                        "draft",
                        "No valid extraction snapshot for repair",
                        disposition="needs_review",
                        action="Inspect the invalid draft",
                    )
                ],
            )
    active = state.get("active_retry")
    if active and not state.get("verification_results"):
        drafts[active.draft.experience_id] = active.draft.model_copy(deep=True)
        results[active.draft.experience_id] = VerificationResult(
            experience_id=active.draft.experience_id,
            decision="reject",
            issues=[
                issue(
                    "minimum",
                    "RETRY_NO_DRAFT",
                    "draft",
                    "Repair produced no supported candidate",
                    disposition="reject",
                    action="Retain the skip reason; do not create a card",
                )
            ],
        )
    docs = state.get("documents", [])
    active_ids = {item.document_id for item in docs}
    chunks = [item for item in state.get("all_chunks", []) if item.document_id not in active_ids]
    chunks.extend(state.get("chunks", []))
    skips = {
        item.experience_id: item
        for item in [*state.get("settled_skips", []), *state.get("draft_skips", [])]
    }
    for key in current:
        skips.pop(key, None)
    return {
        "all_parsed_documents": merge_documents(
            state.get("all_parsed_documents", []), state.get("parsed_documents", [])
        ),
        "all_documents": merge_documents(state.get("all_documents", []), docs),
        "all_chunks": chunks,
        "settled_results": list(results.values()),
        "settled_drafts": list(drafts.values()),
        "settled_skips": list(skips.values()),
        "pending_retries": sorted(
            queue, key=lambda work: TARGET_ORDER.index(work.request.target_node)
        ),
        "active_retry": None,
    }


def next_step(state) -> Literal["prepare_retry", "finish"]:
    return "prepare_retry" if state.get("pending_retries") else "finish"


def prepare_retry(state):
    work, *queue = state["pending_retries"]
    ids = {ref.document_id for ref in work.draft.source_extraction.evidence_index.values()}
    sources = [item for item in state["all_sources"] if item.document_id in ids]
    parsed = [item for item in state["all_parsed_documents"] if item.document_id in ids]
    docs = [item for item in state["all_documents"] if item.document_id in ids]
    chunks = [item for item in state["all_chunks"] if item.document_id in ids]
    target = work.request.target_node
    required = {
        "parse": sources,
        "normalize": parsed,
        "chunk": docs,
        "extract": chunks,
        "draft_card": [work.draft.source_extraction],
    }[target]
    update = {
        "pending_retries": queue,
        "active_retry": work,
        "retry_route": target,
        "sources": sources,
        "parsed_documents": parsed,
        "documents": docs,
        "chunks": chunks,
        "extraction_result": ExtractionResult(
            experiences=[work.draft.source_extraction.model_copy(deep=True)]
        ),
        "drafts": [],
        "draft_skips": [],
        "verified_drafts": [],
        "review_drafts": [],
        "verification_results": [],
        "retry_requests": [],
        "retry_feedback": [work.request],
    }
    if not required:
        update.update(
            retry_route="settle",
            drafts=[work.draft],
            verification_results=[
                VerificationResult(
                    experience_id=work.draft.experience_id,
                    decision="needs_review",
                    issues=[
                        issue(
                            "retry",
                            "RETRY_CONTEXT_MISSING",
                            target,
                            "Required input for the requested node is missing",
                            disposition="needs_review",
                            action="Restore the source input before retrying",
                        )
                    ],
                )
            ],
        )
    return update


def retry_target(state):
    return state["retry_route"]


def finish(state):
    results = state["settled_results"]
    drafts = state["settled_drafts"]
    decisions = {item.experience_id: item.decision for item in results}
    return {
        "sources": state["all_sources"],
        "parsed_documents": state["all_parsed_documents"],
        "documents": state["all_documents"],
        "chunks": state["all_chunks"],
        "extraction_result": ExtractionResult(
            experiences=list(
                {
                    item.experience_id: item.source_extraction
                    for item in [*drafts, *state["settled_skips"]]
                }.values()
            )
        ),
        "drafts": drafts,
        "draft_skips": state["settled_skips"],
        "verification_results": results,
        "verified_drafts": [item for item in drafts if decisions[item.experience_id] == "pass"],
        "review_drafts": [
            item for item in drafts if decisions[item.experience_id] == "needs_review"
        ],
        "retry_requests": [],
        "retry_feedback": [],
        "active_retry": None,
        "verification_history": state["verification_history"],
    }
