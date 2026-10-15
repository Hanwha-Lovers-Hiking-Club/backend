# agent/schemas.py와 역할 동일. 이 도메인(users)의 요청/응답 모양만 다르게 정의.
from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    email: str
