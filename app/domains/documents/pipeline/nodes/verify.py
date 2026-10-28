# 노드: 검증 에이전트. 카드 초안을 통과 / 재작성 요청 / 폐기 중 하나로 판정한다.

from app.domains.documents.pipeline.llm import ConfiguredGenerator, StructuredGenerator
from app.domains.documents.pipeline.state import IngestionState
from app.domains.documents.pipeline.verification.gate import verify_state
from app.domains.documents.pipeline.verification.models import VerificationOptions


def make_verify_node(
    *, options: VerificationOptions | None = None, generator: StructuredGenerator | None = None
):
    async def node(state: IngestionState) -> dict:
        return await verify_state(state, options=options, generator=generator)

    return node


async def verify(state: IngestionState) -> dict:
    from app.config import settings

    options = VerificationOptions(
        rules_enabled=settings.VERIFY_RULES_ENABLED,
        llm_enabled=settings.VERIFY_LLM_ENABLED,
        max_retries=settings.VERIFY_MAX_RETRIES,
        max_input_chars=settings.DOCUMENT_LLM_MAX_INPUT_CHARS,
    )
    generator = ConfiguredGenerator("verify") if options.llm_enabled else None
    return await make_verify_node(options=options, generator=generator)(state)
