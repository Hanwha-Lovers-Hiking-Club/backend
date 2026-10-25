# 문서 도메인의 URL 경로만 정의한다 (업로드 / 목록 / 상세 / 진행상황 SSE).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["documents"])
