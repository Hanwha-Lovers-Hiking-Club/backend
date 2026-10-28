"""Issue bounded, feedback-bearing retry requests; never execute a node here."""

import hashlib
import json

from pydantic import BaseModel

from app.domains.documents.pipeline.verification.models import RetryRequest, VerificationResult
from app.domains.documents.pipeline.verification.structure_checker import issue

TARGET_ORDER = ("parse", "normalize", "chunk", "extract", "draft_card")


def fingerprint(raw, state):
    def serialize(value):
        return value.model_dump(mode="json") if isinstance(value, BaseModel) else str(value)

    payload = {
        "draft": raw,
        "documents": state.get("documents", []),
        "extraction_result": state.get("extraction_result"),
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
