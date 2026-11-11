# 그래프 실행 중 초안 생성 오류를 경험 단위로 기록하고 다른 경험의 결과를 보존한다.

from pydantic import ValidationError

from app.domains.documents.pipeline.extraction import experience_id
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.models import ExtractionResult
from app.domains.documents.pipeline.processing import ProcessingFailure
from app.domains.documents.pipeline.verification.reset import reset_verification


def make_draft_worker(node):
    async def worker(state):
        drafts, skips = [], []
        failures = list(state.get("processing_failures", []))
        for source in state["extraction_result"].experiences:
            key = experience_id(source)
            try:
                update = await node(
                    {**state, "extraction_result": ExtractionResult(experiences=[source])}
                )
                drafts.extend(update["drafts"])
                skips.extend(update["draft_skips"])
                failures = [
                    item
                    for item in failures
                    if not (item.stage == "draft_card" and item.experience_id == key)
                ]
            except (GenerationError, ValidationError) as exc:
                code = exc.code if isinstance(exc, GenerationError) else "LLM_INVALID_OUTPUT"
                for document_id in {ref.document_id for ref in source.evidence_index.values()}:
                    failures.append(
                        ProcessingFailure(
                            document_id=document_id,
                            stage="draft_card",
                            code=code,
                            experience_id=key,
                        )
                    )
        return {
            **reset_verification(),
            "drafts": drafts,
            "draft_skips": skips,
            "processing_failures": failures,
        }

    return worker
