# agent/service.py와 역할 동일. supabase의 users 테이블을 다루는 로직.
from app.db import supabase


async def get_user(user_id: str) -> dict:
    result = supabase.table("users").select("*").eq("id", user_id).single().execute()
    return result.data
