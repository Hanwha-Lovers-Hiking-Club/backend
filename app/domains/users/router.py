# agent/router.py와 역할 동일. 이 파일을 새 도메인 만들 때 템플릿으로 복붙하면 된다.
from fastapi import APIRouter

from app.domains.users import service
from app.domains.users.schemas import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: str):
    return await service.get_user(user_id)
