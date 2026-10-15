# 이 도메인 요청/응답 데이터 모양을 정의하는 곳 (TypeScript interface의 런타임 검증 버전).
# FastAPI가 요청 들어올 때 여기 정의한 필드/타입과 자동으로 맞는지 검사해준다.
from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
