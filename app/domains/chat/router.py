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
        "질문과 이전 대화 내역을 받아 token 이벤트로 답변을 나눠 보내고, references 이벤트"
        "(items: 참조 지식카드, sources: 답변 근거 원문) 후 completed(messageId)로 끝낸다. "
        "생성 중 실패하면 error 이벤트를 보내고 끝낸다. 개발 도메인 밖 질문, 자료로 답할 수 "
        "없는 질문, 너무 막연한 질문(되묻기)은 고정 문장을 token으로 보내고 items/sources는 "
        "빈 목록이다. "
        "원문만 있고 관련 카드가 없으면 원문으로 답하고 items만 빈 목록이다."
    ),
)
async def chat_stream(req: ChatRequest):
    async for event in service.chat_stream(req.message, req.history):
        yield event
