# 이 도메인의 진짜 로직이 사는 곳: DB 호출, LLM 호출, 비즈니스 규칙 전부 여기.
# router.py는 여기 있는 함수를 호출만 하고, 로직은 절대 router.py에 쓰지 않는다.
from app.db import supabase


async def ask_agent(question: str) -> dict:
    # TODO: question을 임베딩 모델로 벡터(query_embedding)로 변환하는 단계가 먼저 필요함
    query_embedding = []  # placeholder

    # pgvector 유사도 검색은 PostgREST(SDK)가 지원하지 않아서,
    # sql/functions.sql 에 만들어둔 Postgres 함수를 rpc()로 호출한다.
    docs = supabase.rpc(  # noqa: F841 (TODO 자리표시용, 아래서 실제로 사용할 예정)
        "match_documents", {"query_embedding": query_embedding}
    ).execute()

    # TODO: docs.data를 컨텍스트로 넣어서 LLM 호출 후 answer 생성
    answer = f"받은 질문: {question}"

    return {"answer": answer}
