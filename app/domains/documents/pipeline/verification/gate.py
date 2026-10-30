# 새 초안을 개별 검증하고 통과·검토 대기·재처리·거절 결과를 구분한다.

from pydantic import ValidationError

from app.domains.documents.pipeline.document import DocumentIR
from app.domains.documents.pipeline.llm import StructuredGenerator
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.state import IngestionState
from app.domains.documents.pipeline.verification.consistency_checker import check_consistency
from app.domains.documents.pipeline.verification.evidence_checker import check_evidence
from app.domains.documents.pipeline.verification.models import RetryRequest, VerificationOptions
from app.domains.documents.pipeline.verification.retry import decide, fingerprint
from app.domains.documents.pipeline.verification.structure_checker import check_structure, issue


async def verify_state(
    state: IngestionState,
    *,
    options: VerificationOptions | None = None,
    generator: StructuredGenerator | None = None,
) -> dict:
    options = VerificationOptions.model_validate((options or VerificationOptions()).model_dump())
    history = [
        RetryRequest.model_validate(item.model_dump())
        for item in state.get("verification_history", [])
    ]
    results, admitted, review, requests = [], [], [], []
    source_issues = []
    try:
        documents = [
            DocumentIR.model_validate(item.model_dump()) for item in state.get("documents", [])
        ]
    except ValidationError:
        documents = []
        source_issues = [
            issue(
                "evidence",
                "INVALID_SOURCE_STATE",
                "documents",
                "Source state no longer satisfies its schema",
                target="normalize",
            )
        ]
    try:
        raw_extraction = state.get("extraction_result")
        extraction = (
            None
            if raw_extraction is None
            else ExtractionResult.model_validate(raw_extraction.model_dump())
        )
    except ValidationError:
        extraction = None
        source_issues.append(
            issue(
                "evidence",
                "INVALID_EXTRACTION_STATE",
                "extraction_result",
                "Current extraction no longer satisfies its schema",
                target="extract",
            )
        )
    candidate_ids = [
        item.get("experience_id")
        if isinstance(item, dict)
        else getattr(item, "experience_id", None)
        for item in state.get("drafts", [])
    ]
    for index, raw in enumerate(state.get("drafts", [])):
        input_key = fingerprint(raw, state)
        draft, issues = check_structure(raw)
        key = draft.experience_id if draft else f"invalid-draft:{index}"
        issues.extend(source_issues)
        if draft is not None and candidate_ids.count(key) > 1:
            issues.append(
                issue(
                    "structure",
                    "DUPLICATE_CANDIDATE",
                    "experience_id",
                    "Multiple drafts represent the same extracted experience",
                    disposition="needs_review",
                    action="Keep one current draft per independent experience",
                )
            )
        if any(item.input_fingerprint == input_key for item in history):
            issues.append(
                issue(
                    "retry",
                    "UNCHANGED_RETRY_INPUT",
                    "draft",
                    "The same source/draft was already scheduled for repair",
                    disposition="needs_review",
                    action="Provide corrected input before another attempt",
                )
            )
        if draft is not None and not issues:
            evidence_issues, records = check_evidence(draft, documents, extraction)
            issues.extend(evidence_issues)
            if not issues:
                issues.extend(await check_consistency(draft, records, options, generator))
        result, request = decide(key, issues, input_key, history, options)
        results.append(result)
        if request:
            history.append(request)
            requests.append(request)
        if result.decision == "pass":
            admitted.append(draft.model_copy(deep=True))
        elif result.decision == "needs_review" and draft is not None:
            review.append(draft.model_copy(deep=True))
    return {
        "embedding_texts": [],
        "embeddings": [],
        "embedding_usage": [],
        "verification_results": results,
        "verified_drafts": admitted,
        "review_drafts": review,
        "retry_requests": requests,
        "retry_feedback": requests,
        "verification_history": history,
    }
