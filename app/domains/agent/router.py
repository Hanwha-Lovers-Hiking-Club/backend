# 이 도메인의 URL 경로만 정의하는 곳. 로직은 절대 여기 쓰지 않고 service.py의 함수를 호출만 한다.
from fastapi import APIRouter

from app.domains.agent import service
from app.domains.agent.schemas import AskRequest, AskResponse

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    return await service.ask_agent(req.question)
