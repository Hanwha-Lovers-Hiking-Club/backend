# 노드: 청크에서 지식카드 구성 요소(문제/원인/접근/교훈 등)를 LLM으로 추출한다.
#
# job에 속한 모든 청크를 한 번에 LLM에 보여주고, "같은 경험"끼리 묶어서(M:N) 요소를 추출하게 한다.
# 맵-리듀스는 하지 않고 모델 컨텍스트 윈도우 크기에 의존한다 — 넘치면 job 전체를 에러로 중단한다.
import logging
from pathlib import Path

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage

from app.domains.documents.pipeline.chunk_formatting import format_chunks
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import ExtractedElements, ExtractedElementsBatch
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "extract.md").read_text(encoding="utf-8")
_tokenizer = tiktoken.get_encoding("cl100k_base")

# ponytail: 보수적으로 잡은 임시값(gpt-4.1-mini 컨텍스트 윈도우 ~1.05M 토큰 대비 넉넉한 여유).
# 전체 파이프라인을 테스트하면서 실측 기반으로 조정한다.
MAX_INPUT_TOKENS = 200_000


def _keep_valid_chunk_ids(
    elements: list[ExtractedElements], chunks: list[Chunk]
) -> list[ExtractedElements]:
    # LLM이 지어낸/오타 난 chunk id를 걸러낸다. 하나도 안 남는 경험은 근거가 없으므로 버린다.
    # draft_card가 조용히 건너뛰던 경우를 여기서 먼저, 로그를 남기고 처리한다.
    valid_ids = {c["id"] for c in chunks}
    kept: list[ExtractedElements] = []
    for element in elements:
        ids = list(dict.fromkeys(i for i in element.source_chunk_ids if i in valid_ids))
        if len(ids) != len(element.source_chunk_ids):
            logger.warning(
                "extract: 무효/중복 chunk id 제거 %s -> %s", element.source_chunk_ids, ids
            )
        if not ids:
            logger.warning("extract: 근거 chunk id가 없어 경험 폐기 (problem=%r)", element.problem)
            continue
        kept.append(element.model_copy(update={"source_chunk_ids": ids}))
    return kept


def extract(state: IngestionState) -> dict:
    # 청크가 없으면 LLM에 빈 입력을 주지 않는다(빈 입력에서 내용을 지어낼 수 있음)
    if not state["chunks"]:
        return {"extracted_elements": []}

    formatted = format_chunks(state["chunks"])
    if len(_tokenizer.encode(formatted)) > MAX_INPUT_TOKENS:
        return {"error": f"추출 입력이 너무 큽니다 (토큰 {MAX_INPUT_TOKENS}개 초과)"}

    llm = get_llm().with_structured_output(ExtractedElementsBatch)
    try:
        # 지시문은 시스템 메시지, 문서 본문은 사용자 메시지로 분리해서
        # 본문 속 문장이 지시문과 섞이지 않게 한다
        result = invoke_with_retry(llm, [SystemMessage(_PROMPT), HumanMessage(formatted)])
    except Exception as e:
        return {"error": f"요소 추출 실패: {e}"}

    elements = _keep_valid_chunk_ids(result.elements, state["chunks"])
    logger.info(
        "extract: 청크 %d개 -> 경험 %d개 (LLM 응답 %d개)",
        len(state["chunks"]),
        len(elements),
        len(result.elements),
    )
    return {"extracted_elements": elements}
