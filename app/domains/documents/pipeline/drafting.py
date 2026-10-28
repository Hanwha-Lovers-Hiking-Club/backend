"""Write one unverified draft per supported independent experience."""

from app.domains.documents.pipeline.extraction import experience_id, prompt_text
from app.domains.documents.pipeline.llm import StructuredGenerator, generate_structured
from app.domains.documents.pipeline.models import (
    FACT_FIELDS,
    DraftProposal,
    DraftSkip,
    KnowledgeCardDraft,
)
from app.domains.documents.pipeline.state import IngestionState


def _skip_reason(source):
    if source.context.status != "confirmed":
        return "NO_CONFIRMED_CONTEXT", "Confirmed context is required to write a card"
    if not any(getattr(source, name).status == "confirmed" for name in FACT_FIELDS[1:-1]):
        return "NO_EXPERIENCE_FACTS", "No confirmed problem-solving information"
    if source.lesson.status not in {"confirmed", "inferred"} and not (
        source.approach.status == "confirmed" and source.outcome.status == "confirmed"
    ):
        return "NO_SUPPORTED_LESSON", "Need a supported lesson or confirmed approach and outcome"
    return None


async def draft_state(
    state: IngestionState, generator: StructuredGenerator, *, max_input_chars: int = 48000
) -> dict:
    drafts, skipped, seen = [], [], set()
    for source in state["extraction_result"].experiences:
        key = experience_id(source)
        if key in seen:
            continue
        seen.add(key)
        reason = _skip_reason(source)
        if reason is None:
            proposal = await generate_structured(
                generator,
                instructions=prompt_text("draft_card"),
                output_type=DraftProposal,
                max_input_chars=max_input_chars,
                payload={"experience_id": key, "extraction": source.model_dump(mode="json")},
            )
            if proposal.draft is None:
                reason = ("MODEL_DECLINED", proposal.skip_reason)
        if reason is not None:
            skipped.append(
                DraftSkip(
                    experience_id=key,
                    code=reason[0],
                    reason=reason[1],
                    source_extraction=source.model_copy(deep=True),
                )
            )
            continue
        text = proposal.draft
        body = {
            name: fact.value if fact.status == "confirmed" else None
            for name in FACT_FIELDS[:-1]
            for fact in [getattr(source, name)]
        }
        field_refs = {
            name: list(getattr(source, name).evidence_refs) if body[name] is not None else []
            for name in FACT_FIELDS[:-1]
        }
        all_refs = sorted(source.evidence_index)
        lesson_refs = (
            source.lesson.evidence_refs
            if source.lesson.status in {"confirmed", "inferred"}
            else sorted(set(source.approach.evidence_refs + source.outcome.evidence_refs))
        )
        field_refs.update(title=all_refs, summary=all_refs, lesson=list(lesson_refs))
        drafts.append(
            KnowledgeCardDraft(
                experience_id=key,
                source_extraction=source.model_copy(deep=True),
                field_evidence_refs=field_refs,
                lesson_status=(
                    "confirmed"
                    if source.lesson.status == "confirmed" and text.lesson == source.lesson.value
                    else "inferred"
                ),
                **body,
                title=text.title,
                summary=text.summary,
                lesson=text.lesson,
                technologies=list(source.technologies),
                keywords=list(dict.fromkeys(source.keywords + text.keywords)),
            )
        )
    # Leave extraction_result intact; the verification gate needs the original assertions.
    return {"drafts": drafts, "draft_skips": skipped}
