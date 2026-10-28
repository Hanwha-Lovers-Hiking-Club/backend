# 노드: 추출된 요소로 지식카드 초안을 만들고 근거(location/excerpt)를 연결한다.

from app.domains.documents.pipeline.drafting import draft_state
from app.domains.documents.pipeline.llm import ConfiguredGenerator, StructuredGenerator
from app.domains.documents.pipeline.state import IngestionState


def make_draft_node(generator: StructuredGenerator, *, max_input_chars: int = 48000):
    async def node(state: IngestionState) -> dict:
        return await draft_state(state, generator, max_input_chars=max_input_chars)

    return node


async def draft_card(state: IngestionState) -> dict:
    from app.config import settings

    return await make_draft_node(
        ConfiguredGenerator("draft"), max_input_chars=settings.DOCUMENT_LLM_MAX_INPUT_CHARS
    )(state)
