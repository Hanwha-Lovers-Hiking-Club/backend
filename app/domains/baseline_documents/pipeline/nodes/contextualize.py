# 노드: 청크마다 LLM으로 "이 청크가 문서에서 어떤 내용인지" 1~2문장 설명을 만들고
# (contextual retrieval),
# 문서 제목·섹션 경로 헤더 + 설명 + 본문을 합쳐 검색용 텍스트(search_text)를 만든다.
#
# 메시지 앞부분(지시문 + 문서 전문)을 같은 문서의 모든 청크에서 똑같이 두고 청크만 맨 뒤에 붙인다.
# 이렇게 하면 OpenAI 자동 프롬프트 캐싱이 적용되어, 문서 전문을 청크마다 다시 보내는 비용이
# 줄어든다.
import asyncio
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.domains.baseline_documents.pipeline.state import (
    Chunk,
    ContextualizedChunk,
    ParsedDocument,
)
from app.domains.documents.pipeline.llm import get_llm
from app.domains.documents.pipeline.nodes.chunk import _tokenizer

_PROMPT = (Path(__file__).parent.parent / "prompts" / "contextualize.md").read_text(
    encoding="utf-8"
)

# main의 extract 노드와 같은 기준. 이보다 긴 문서는 문서 전문을 프롬프트에 넣을 수 없어서 중단한다
MAX_DOCUMENT_TOKENS = 200_000
# 동시에 보내는 LLM 요청 수 상한 (rate limit 방지)
MAX_CONCURRENCY = 5


def build_header(title: str, location: str) -> str:
    return f"{title} · {location}" if location else title


def build_search_text(header: str, context: str, content: str) -> str:
    return f"{header}\n{context}\n\n{content}"


def build_messages(document_text: str, chunk_text: str) -> list:
    return [
        SystemMessage(content=_PROMPT),
        HumanMessage(
            content=f"<document>\n{document_text}\n</document>\n\n<chunk>\n{chunk_text}\n</chunk>"
        ),
    ]


async def _describe(
    semaphore: asyncio.Semaphore, document: ParsedDocument, chunk: Chunk
) -> ContextualizedChunk:
    async with semaphore:
        response = await get_llm().ainvoke(build_messages(document["text"], chunk["content"]))
    header = build_header(document["title"], chunk["location"])
    context = str(response.content).strip()
    return {**chunk, "search_text": build_search_text(header, context, chunk["content"])}


async def contextualize(state: dict) -> dict:
    documents: dict = {document["document_id"]: document for document in state["parsed_documents"]}

    for document in documents.values():
        if len(_tokenizer.encode(document["text"])) > MAX_DOCUMENT_TOKENS:
            return {
                "error": f"'{document['title']}' 문서가 너무 깁니다 "
                f"(토큰 {MAX_DOCUMENT_TOKENS}개 초과)"
            }

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    try:
        # gather는 입력 순서대로 결과를 돌려주므로 청크 순서가 유지된다
        contextualized = await asyncio.gather(
            *(
                _describe(semaphore, documents[chunk["document_id"]], chunk)
                for chunk in state["chunks"]
            )
        )
    except Exception as e:
        # 설명 없이 저장하면 비교 결과가 조용히 나빠지므로, 일부만 실패해도 전체를 중단한다
        return {"error": f"청크 문맥 설명 생성 실패: {e}"}

    return {"contextualized_chunks": list(contextualized)}
