# 원문과의 보수적인 문자열 대조와 선택적인 LLM 기반 의미 일치 검사를 수행한다.

import hashlib
import json
import re

from pydantic import ValidationError

from app.domains.documents.pipeline.call_retries import transient_failure
from app.domains.documents.pipeline.extraction import prompt_text
from app.domains.documents.pipeline.llm import GenerationError, generate_structured
from app.domains.documents.pipeline.models import FACT_FIELDS
from app.domains.documents.pipeline.verification.context import build_review_context
from app.domains.documents.pipeline.verification.models import (
    Claim,
    SemanticReport,
    VerificationTrace,
)
from app.domains.documents.pipeline.verification.structure_checker import issue


def build_claims(draft):
    claims = []
    for name in FACT_FIELDS[:-1]:
        value = getattr(draft, name)
        if value is not None:
            claims.append(
                Claim(
                    key=f"extract.{name}",
                    field=name,
                    value=value,
                    status="confirmed",
                    evidence_refs=draft.field_evidence_refs[name],
                )
            )
    for name in ("title", "summary", "lesson"):
        claims.append(
            Claim(
                key=f"draft.{name}",
                field=name,
                value=getattr(draft, name),
                status=draft.lesson_status if name == "lesson" else "confirmed",
                evidence_refs=draft.field_evidence_refs[name],
            )
        )
    for index, technology in enumerate(draft.technologies):
        claims.append(
            Claim(
                key=f"technology.{index}",
                field="technologies",
                value=technology,
                status="confirmed",
                evidence_refs=sorted(draft.source_extraction.evidence_index),
            )
        )
    return claims


def normalize_text(text):
    return " ".join(text.split())


UNCERTAINTY = (
    r"\b(might|maybe|possibly|hypothesis|suspect(?:ed)?|unconfirmed)\b|가설|추정|의심|가능성|미확인"
)


def literal_match(claim, records):
    for ref in claim.evidence_refs:
        text = records[ref]["evidence"]["excerpt"]
        if claim.field == "technologies":
            if re.search(r"(?<!\w)" + re.escape(claim.value) + r"(?!\w)", text, re.IGNORECASE):
                return True
        else:
            # Do not detach a sentence/line from surrounding uncertainty or rebuttal.
            if normalize_text(claim.value) != normalize_text(text) and re.search(
                UNCERTAINTY + r"|\b(not|no|however|actually|contradict\w*)\b|반박|상충|아니|않",
                text,
                re.IGNORECASE,
            ):
                continue
            # Substring matching would accept 'A caused it' inside 'A caused it? No.'
            units = [text, *re.split(r"(?<=[.!?。])\s+|\r?\n", text)]
            if normalize_text(claim.value) in {normalize_text(unit) for unit in units}:
                return True
    return False


async def check_consistency(
    draft, records, options, generator, *, documents=(), traces=None, input_fingerprint=None
):
    issues = []
    for name in FACT_FIELDS:
        fact = getattr(draft.source_extraction, name)
        if fact.status == "conflicted":
            issues.append(
                issue(
                    "consistency",
                    "CONFLICTED_SOURCE",
                    name,
                    "Extraction retains unresolved conflicting evidence",
                    disposition="needs_review",
                    action="Resolve the conflict without selecting a convenient claim",
                )
            )
        if (
            fact.status in {"hypothesis", "inferred"}
            and fact.value
            and not re.search(UNCERTAINTY, fact.value, re.IGNORECASE)
        ):
            for field in ("title", "summary"):
                if normalize_text(getattr(draft, field)) == normalize_text(fact.value):
                    issues.append(
                        issue(
                            "consistency",
                            "UNCERTAINTY_PROMOTED",
                            field,
                            "An unqualified title/summary repeats a hypothesized or inferred value",
                            target="draft_card",
                        )
                    )
    cause = draft.source_extraction.cause
    if cause.status == "confirmed" and re.search(
        UNCERTAINTY + r"|[?？]",
        cause.value,
        re.IGNORECASE,
    ):
        issues.append(
            issue(
                "consistency",
                "HYPOTHESIS_AS_FACT",
                "cause",
                "Confirmed cause still contains an uncertainty marker",
                target="extract",
            )
        )
    if issues:
        return issues
    claims = build_claims(draft)
    if not options.llm_enabled:
        return [
            issue(
                "consistency",
                "SEMANTIC_CHECK_REQUIRED",
                claim.field,
                "Rules cannot establish this claim from a complete literal source unit",
                disposition="needs_review",
                action="Review the source or explicitly enable LLM verification",
            )
            for claim in claims
            if not literal_match(claim, records)
        ]
    if generator is None:
        return [
            issue(
                "consistency",
                "VERIFIER_NOT_CONFIGURED",
                "draft",
                "LLM verification is enabled but no verifier is configured",
                disposition="needs_review",
                action="Configure VERIFY_MODEL and the selected provider",
            )
        ]
    instructions = prompt_text("verify")
    payload = {
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "source_extraction": draft.source_extraction.model_dump(mode="json"),
        "sources": records,
    }
    context = build_review_context(
        draft,
        records,
        documents,
        max_chars=options.context_max_chars,
        request_budget=options.max_input_chars - len(instructions),
        base_payload=payload,
    )
    if context is not None:
        payload["review_context"] = context
    document_ids = {ref.document_id for ref in draft.source_extraction.evidence_index.values()}
    coverage = {}
    for document_id in document_ids:
        document = next((doc for doc in documents if doc.document_id == document_id), None)
        coverage[str(document_id)] = (
            context["documents"][str(document_id)]["coverage"]
            if context is not None
            else "complete"
            if document is not None and all(b.evidence_id in records for b in document.blocks)
            else "partial"
            if document is not None
            else "unavailable"
        )
    serialized = json.dumps(payload, ensure_ascii=False)
    trace = VerificationTrace(
        experience_id=draft.experience_id,
        input_fingerprint=input_fingerprint or hashlib.sha256(serialized.encode()).hexdigest(),
        supporting_evidence_ids=sorted(records),
        context_block_ids=[item["block_id"] for item in (context or {}).get("blocks", {}).values()],
        document_coverage=coverage,
        input_chars=len(instructions) + len(serialized),
        input_sha256=hashlib.sha256((instructions + serialized).encode()).hexdigest(),
    )
    if traces is not None:
        traces.append(trace)
    try:
        report = await generate_structured(
            generator,
            instructions=instructions,
            output_type=SemanticReport,
            max_input_chars=options.max_input_chars,
            payload=payload,
        )
    except (GenerationError, ValidationError) as exc:
        code = exc.code if isinstance(exc, GenerationError) else "LLM_INVALID_OUTPUT"
        unavailable = isinstance(exc, GenerationError) and transient_failure(exc)
        trace.error_code = "VERIFIER_UNAVAILABLE" if unavailable else code
        return [
            issue(
                "consistency",
                "VERIFIER_UNAVAILABLE" if unavailable else code,
                "draft",
                f"Semantic verification did not complete ({code})",
                disposition="needs_review",
                action="Inspect the verifier failure; do not treat it as a pass",
            )
        ]
    by_key = {claim.key: claim for claim in claims}
    keys = [item.key for item in report.assessments]
    trace.assessments = report.assessments
    context_ids = set((context or {}).get("blocks", {})) | records.keys()
    if (
        len(keys) != len(set(keys))
        or set(keys) != by_key.keys()
        or any(
            set(item.evidence_refs) - set(by_key[item.key].evidence_refs)
            for item in report.assessments
            if item.key in by_key
        )
        or any(set(item.counter_evidence_refs) - context_ids for item in report.assessments)
    ):
        trace.error_code = "INVALID_VERIFIER_REPORT"
        return [
            issue(
                "consistency",
                "INVALID_VERIFIER_REPORT",
                "draft",
                "Verifier omitted/duplicated claims or cited unassigned evidence",
                disposition="needs_review",
                action="Inspect the verifier output",
            )
        ]
    trace.report_valid = True
    complete_context = bool(coverage) and all(value == "complete" for value in coverage.values())
    for item in report.assessments:
        if item.verdict == "supported" and item.requires_document_scope and not complete_context:
            trace.error_code = "DOCUMENT_SCOPE_NOT_VERIFIED"
            issues.append(
                issue(
                    "consistency",
                    "DOCUMENT_SCOPE_NOT_VERIFIED",
                    by_key[item.key].field,
                    "A document-wide claim cannot pass with partial review coverage",
                    disposition="needs_review",
                    action="Provide the missing context or restrict the claim to supported facts",
                )
            )
            continue
        if item.verdict == "supported":
            continue
        claim = by_key[item.key]
        target = "extract" if item.key.startswith(("extract.", "technology.")) else "draft_card"
        repairable = item.verdict == "unsupported"
        if item.verdict != "conflicted" and item.failure_kind == "context_missing":
            # 현재 청킹은 동일 입력·설정에 결정적이다.
            # 문맥 확장 전략 없이 유료 호출을 반복하지 않는다.
            target, repairable = None, False
        elif item.verdict != "conflicted" and item.failure_kind == "source_unreadable":
            target, repairable = "parse", True
        issues.append(
            issue(
                "consistency",
                {
                    "unsupported": "UNSUPPORTED_CLAIM",
                    "uncertain": "UNCERTAIN_CLAIM",
                    "conflicted": "CONFLICTING_CLAIM",
                }[item.verdict],
                claim.field,
                item.reason,
                target=target if repairable else None,
                disposition="retry" if repairable else "needs_review",
                action="Re-extract/rewrite against the cited source; preserve uncertainty",
            )
        )
    return issues
