# 수정된 모델이나 model_construct로 만든 입력도 Gate 진입 시 구조와 최소 요건을 다시 검증한다.

from pydantic import BaseModel, ValidationError

from app.domains.documents.pipeline.models import FACT_FIELDS, KnowledgeCardDraft
from app.domains.documents.pipeline.verification.models import VerificationIssue


def issue(check, code, field, reason, *, target=None, disposition="retry", action=None):
    return VerificationIssue(
        check=check,
        code=code,
        field=field,
        reason=reason,
        action=action or f"Reprocess {target} with the failure feedback",
        disposition=disposition,
        retry_node=target,
    )


def check_structure(raw):
    try:
        data = raw.model_dump() if isinstance(raw, BaseModel) else raw
        draft = KnowledgeCardDraft.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        return None, [
            issue(
                "structure",
                "INVALID_DRAFT",
                ".".join(map(str, error["loc"])) or "draft",
                error["msg"],
                target="extract"
                if error["loc"] and error["loc"][0] == "source_extraction"
                else "draft_card",
            )
            for error in errors
        ]
    if not any(getattr(draft, name) is not None for name in FACT_FIELDS[1:-1]):
        return draft, [
            issue(
                "minimum",
                "INSUFFICIENT_EXPERIENCE",
                "draft",
                "At least one confirmed problem/cause/approach/rationale/outcome is required",
                disposition="reject",
                action="Do not create a card from insufficient information",
            )
        ]
    return draft, []
