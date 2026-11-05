# 노드: 청크에서 지식카드 구성 요소(문제/원인/접근/교훈 등)를 LLM으로 추출한다.

from app.domains.documents.pipeline.extraction import extract_state
from app.domains.documents.pipeline.llm import ConfiguredGenerator, StructuredGenerator
from app.domains.documents.pipeline.state import IngestionState


def make_extract_node(generator: StructuredGenerator, *, max_input_chars: int = 48000):
    async def node(state: IngestionState) -> dict:
        return await extract_state(state, generator, max_input_chars=max_input_chars)

    return node


async def extract(state: IngestionState) -> dict:
    from app.config import settings

    return await make_extract_node(
        ConfiguredGenerator("extract"), max_input_chars=settings.DOCUMENT_LLM_MAX_INPUT_CHARS
    )(state)
