# 챗봇 도메인의 URL 경로만 정의한다 (답변 스트리밍 SSE).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from fastapi import APIRouter
from fastapi.sse import EventSourceResponse

from app.domains.chat import service
from app.domains.chat.schemas import ChatRequest

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    summary="챗봇 답변 생성 (SSE)",
    description=(
        "질문과 이전 대화 내역을 받아 token 이벤트로 답변을 나눠 보내고, references(참조 지식카드·"
        "근거) 이벤트 후 completed로 끝낸다. 생성 중 실패하면 error 이벤트를 보내고 끝낸다. "
        "지금 답변과 근거는 Mock이다."
    ),
)
async def chat_stream(req: ChatRequest):
    async for event in service.chat_stream(req.message, req.history):
        yield event
