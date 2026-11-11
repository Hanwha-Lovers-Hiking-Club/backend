# 이 도메인의 진짜 로직이 사는 곳. 검색 RPC와 질문 임베딩 계약이 확정되면 연결한다.
from fastapi import HTTPException


async def ask_agent(question: str) -> dict:
    # 아직 질문 임베딩·카드 검색 RPC·응답 생성이 구현되지 않았다.
    # 빈 벡터로 없는 RPC를 호출하거나 자리표시자 답변을 성공으로 돌려주지 않는다.
    raise HTTPException(status_code=503, detail="지식카드 검색 기능이 아직 준비되지 않았습니다.")
