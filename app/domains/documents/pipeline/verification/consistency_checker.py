"""Conservative literal checks and optional source-grounded semantic checks."""

import re

from pydantic import ValidationError

from app.domains.documents.pipeline.extraction import prompt_text
from app.domains.documents.pipeline.llm import GenerationError, generate_structured
from app.domains.documents.pipeline.models import FACT_FIELDS
from app.domains.documents.pipeline.verification.models import Claim, SemanticReport
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


async def check_consistency(draft, records, options, generator):
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
    try:
        report = await generate_structured(
            generator,
            instructions=prompt_text("verify"),
            output_type=SemanticReport,
            max_input_chars=options.max_input_chars,
            payload={
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "source_extraction": draft.source_extraction.model_dump(mode="json"),
                "sources": records,
            },
        )
    except (GenerationError, ValidationError) as exc:
        code = exc.code if isinstance(exc, GenerationError) else "LLM_INVALID_OUTPUT"
        return [
            issue(
                "consistency",
                code,
                "draft",
                "Semantic verification did not complete",
                disposition="needs_review",
                action="Inspect the verifier failure; do not treat it as a pass",
            )
        ]
    by_key = {claim.key: claim for claim in claims}
    keys = [item.key for item in report.assessments]
    if (
        len(keys) != len(set(keys))
        or set(keys) != by_key.keys()
        or any(
            set(item.evidence_refs) - set(by_key[item.key].evidence_refs)
            for item in report.assessments
            if item.key in by_key
        )
    ):
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
    for item in report.assessments:
        if item.verdict == "supported":
            continue
        claim = by_key[item.key]
        target = "extract" if item.key.startswith(("extract.", "technology.")) else "draft_card"
        repairable = item.verdict == "unsupported"
        if item.verdict != "conflicted" and item.failure_kind == "context_missing":
            target, repairable = "chunk", True
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
