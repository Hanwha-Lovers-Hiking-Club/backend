# 횟수 제한과 피드백을 포함한 재처리 요청을 만든다. 실제 노드 실행은 그래프가 담당한다.

import hashlib
import json

from pydantic import BaseModel

from app.domains.documents.pipeline.verification.models import RetryRequest, VerificationResult
from app.domains.documents.pipeline.verification.structure_checker import issue

TARGET_ORDER = ("parse", "normalize", "chunk", "extract", "draft_card")


def fingerprint(raw, state):
    def serialize(value):
        return value.model_dump(mode="json") if isinstance(value, BaseModel) else str(value)

    data = raw.model_dump(mode="json") if isinstance(raw, BaseModel) else raw
    source = data.get("source_extraction", {}) if isinstance(data, dict) else {}
    ids = {str(ref.get("document_id")) for ref in source.get("evidence_index", {}).values()}
    extraction = state.get("extraction_result")
    payload = {
        "draft": data,
        "documents": [
            item for item in state.get("documents", []) if not ids or str(item.document_id) in ids
        ],
        "snapshot_present": extraction is not None
        and any(item.model_dump(mode="json") == source for item in extraction.experiences),
    }
    return hashlib.sha256(
        json.dumps(payload, default=serialize, sort_keys=True).encode()
    ).hexdigest()


def decide(experience_id, issues, input_fingerprint, history, options):
    if not issues:
        return VerificationResult(experience_id=experience_id, decision="pass"), None
    if any(item.disposition == "reject" for item in issues):
        decision, target = "reject", None
    elif any(item.disposition == "needs_review" for item in issues):
        decision, target = "needs_review", None
    else:
        decision = "retry"
        target = next(
            node for node in TARGET_ORDER if any(item.retry_node == node for item in issues)
        )
        repeated = any(
            item.input_fingerprint == input_fingerprint and item.target_node == target
            for item in history
        )
        if repeated or len(history) >= options.max_retries:
            decision, target = "needs_review", None
            issues = [
                *issues,
                issue(
                    "retry",
                    "UNCHANGED_RETRY_INPUT" if repeated else "RETRY_LIMIT_REACHED",
                    "draft",
                    "The same source/draft was already scheduled"
                    if repeated
                    else "Job retry budget is exhausted",
                    disposition="needs_review",
                    action="Supply corrected input or resolve the issue manually",
                ),
            ]
    result = VerificationResult(
        experience_id=experience_id, decision=decision, issues=issues, retry_node=target
    )
    request = (
        None
        if target is None
        else RetryRequest(
            experience_id=experience_id,
            target_node=target,
            input_fingerprint=input_fingerprint,
            issues=issues,
        )
    )
    return result, request
