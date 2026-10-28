"""Extract independent experiences and bind only code-owned source evidence."""

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.domains.documents.pipeline.llm import (
    GenerationError,
    StructuredGenerator,
    generate_structured,
)
from app.domains.documents.pipeline.models import (
    FACT_FIELDS,
    ExtractedElements,
    ExtractionProposal,
    ExtractionResult,
)
from app.domains.documents.pipeline.state import IngestionState


def experience_id(experience: ExtractedElements) -> str:
    canonical = json.dumps(experience.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return str(
        uuid5(NAMESPACE_URL, "lessons-learned:" + hashlib.sha256(canonical.encode()).hexdigest())
    )


def prompt_text(name: str) -> str:
    return (Path(__file__).parent / "prompts" / f"{name}.md").read_text(encoding="utf-8")


async def extract_state(
    state: IngestionState, generator: StructuredGenerator, *, max_input_chars: int = 48000
) -> dict:
    documents = {document.document_id: document for document in state["documents"]}
    if len(documents) != len(state["documents"]):
        raise GenerationError("DUPLICATE_DOCUMENT", "Document IDs must be unique in this run")
    experiences, seen, seen_chunks = [], set(), {}
    for chunk in state["chunks"]:
        if chunk.chunk_id in seen_chunks:
            if chunk != seen_chunks[chunk.chunk_id]:
                raise GenerationError("CHUNK_ID_COLLISION", "One chunk ID has different content")
            continue
        seen_chunks[chunk.chunk_id] = chunk
        document = documents.get(chunk.document_id)
        if document is None:
            raise GenerationError("UNKNOWN_DOCUMENT", "Chunk refers to an unavailable document")
        by_id = {block.block_id: block for block in document.blocks}
        allowed = {}
        for block in chunk.blocks:
            original = by_id.get(block.block_id)
            if original is None or original != block:
                raise GenerationError("INVALID_CHUNK_SOURCE", "Chunk differs from its source block")
            allowed[block.evidence_id] = document.evidence_index[block.evidence_id]
        proposal = await generate_structured(
            generator,
            instructions=prompt_text("extract"),
            output_type=ExtractionProposal,
            max_input_chars=max_input_chars,
            payload={
                "document_id": str(chunk.document_id),
                "original_filename": document.original_filename,
                "chunk_id": chunk.chunk_id,
                "section_path": chunk.section_path,
                "source_warnings": document.warnings,
                "blocks": [block.model_dump(mode="json") for block in chunk.blocks],
            },
        )
        for candidate in proposal.experiences:
            refs = {ref for name in FACT_FIELDS for ref in getattr(candidate, name).evidence_refs}
            if refs - allowed.keys():
                raise GenerationError("UNKNOWN_EVIDENCE", "Model cited evidence outside this chunk")
            bound = ExtractedElements(
                **candidate.model_dump(),
                evidence_index={key: allowed[key].model_copy(deep=True) for key in sorted(refs)},
            )
            key = experience_id(bound)
            if key not in seen:
                seen.add(key)
                experiences.append(bound)
    return {
        "extraction_result": ExtractionResult(experiences=experiences),
        "drafts": [],
        "draft_skips": [],
    }
