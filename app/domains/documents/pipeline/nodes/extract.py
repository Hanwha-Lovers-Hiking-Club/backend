# 노드: 청크에서 지식카드 구성 요소(문제/원인/접근/교훈 등)를 LLM으로 추출한다.
#
# job에 속한 모든 청크를 한 번에 LLM에 보여주고, "같은 경험"끼리 묶어서(M:N) 요소를 추출하게 한다.
# 맵-리듀스는 하지 않고 모델 컨텍스트 윈도우 크기에 의존한다 — 넘치면 job 전체를 에러로 중단한다.
from pathlib import Path

import tiktoken

from app.domains.documents.pipeline.llm import get_llm
from app.domains.documents.pipeline.models import ExtractedElementsBatch
from app.domains.documents.pipeline.state import IngestionState

_PROMPT = (Path(__file__).parent.parent / "prompts" / "extract.md").read_text(encoding="utf-8")
_tokenizer = tiktoken.get_encoding("cl100k_base")

# ponytail: 보수적으로 잡은 임시값(gpt-4.1-mini 컨텍스트 윈도우 ~1.05M 토큰 대비 넉넉한 여유).
# 전체 파이프라인을 테스트하면서 실측 기반으로 조정한다.
MAX_INPUT_TOKENS = 200_000


def _format_chunks(state: IngestionState) -> str:
    return "\n".join(
        f'<chunk id="{c["id"]}" location="{c["location"]}">\n{c["text"]}\n</chunk>'
        for c in state["chunks"]
    )


def extract(state: IngestionState) -> dict:
    formatted = _format_chunks(state)
    if len(_tokenizer.encode(formatted)) > MAX_INPUT_TOKENS:
        return {"error": f"추출 입력이 너무 큽니다 (토큰 {MAX_INPUT_TOKENS}개 초과)"}

    llm = get_llm().with_structured_output(ExtractedElementsBatch)
    try:
        result = llm.invoke(f"{_PROMPT}\n{formatted}")
    except Exception as e:
        return {"error": f"요소 추출 실패: {e}"}

    return {"extracted_elements": result.elements}
