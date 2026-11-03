# 기본 RAG 챗봇 도메인의 URL 경로만 정의한다 (답변 스트리밍 SSE).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from fastapi import APIRouter
from fastapi.sse import EventSourceResponse

from app.domains.baseline_chat import service
from app.domains.chat.schemas import ChatRequest

router = APIRouter(prefix="/baseline/chat", tags=["baseline"])


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    summary="[기본 RAG] 챗봇 답변 생성 (SSE)",
    description=(
        "지식카드 효과 비교용. 지식카드 없이 baseline_chunks만 검색해서 답한다. "
        "요청 모양과 이벤트 순서는 /api/chat/stream과 같다: token(여러 번) -> references -> "
        "completed, 실패하면 error. references는 지식카드 대신 문서 청크(인용 번호, 발췌문, "
        "점수)를 담는다."
    ),
)
async def chat_stream(req: ChatRequest):
    async for event in service.chat_stream(req.message, req.history):
        yield event
