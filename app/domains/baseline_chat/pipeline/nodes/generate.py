# 노드: 선택한 청크를 컨텍스트로 넣어 LLM 답변을 토큰 단위로 스트리밍한다.
# 답변에는 [1] 형식으로 근거 청크 번호를 인용한다. 번호는 contexts의 순서(1부터)와 같다.
#
# 토큰을 바로바로 흘려보내야 해서 LangGraph 노드가 아니라 async generator로 만들고,
# service.py가 검색 그래프를 실행한 뒤 직접 호출한다.
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.domains.baseline_chat.pipeline.state import ContextChunk
from app.domains.chat.schemas import ChatMessage
from app.domains.documents.pipeline.llm import get_llm

_PROMPT = (Path(__file__).parent.parent / "prompts" / "answer.md").read_text(encoding="utf-8")

# 답변 생성에 넘기는 최근 대화 수 (rewrite와 같은 기준)
MAX_HISTORY_MESSAGES = 6
# 검색 결과가 하나도 없을 때는 LLM을 부르지 않고 이 문장으로 답한다
NO_CONTEXT_ANSWER = "질문과 관련된 내용을 문서에서 찾지 못했습니다."


def format_contexts(contexts: list[ContextChunk], titles: dict[str, str]) -> str:
    blocks = []
    for number, chunk in enumerate(contexts, start=1):
        title = titles.get(chunk["document_id"], "제목 없음")
        heading = f"{title} · {chunk['location']}" if chunk["location"] else title
        blocks.append(f"[{number}] {heading}\n{chunk['context']}")
    return "\n\n".join(blocks)


def build_messages(
    message: str, history: list[ChatMessage], contexts: list[ContextChunk], titles: dict[str, str]
) -> list:
    messages: list = [SystemMessage(content=_PROMPT)]
    for past in history[-MAX_HISTORY_MESSAGES:]:
        cls = HumanMessage if past.role == "user" else AIMessage
        messages.append(cls(content=past.content))
    messages.append(
        HumanMessage(
            content=f"<documents>\n{format_contexts(contexts, titles)}\n</documents>\n\n"
            f"<question>\n{message}\n</question>"
        )
    )
    return messages


async def generate(
    message: str, history: list[ChatMessage], contexts: list[ContextChunk], titles: dict[str, str]
) -> AsyncIterator[str]:
    if not contexts:
        yield NO_CONTEXT_ANSWER
        return

    async for chunk in get_llm().astream(build_messages(message, history, contexts, titles)):
        if chunk.content:
            yield str(chunk.content)
