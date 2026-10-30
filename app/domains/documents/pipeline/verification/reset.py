# 상위 노드 재실행 시 이전 검증 결과와 임베딩 입력·벡터·사용량을 초기화한다.
# 같은 작업의 재처리 이력과 피드백은 유지한다.


def reset_verification() -> dict:
    return {
        "embedding_texts": [],
        "embeddings": [],
        "embedding_usage": [],
        "verification_results": [],
        "verified_drafts": [],
        "review_drafts": [],
        "retry_requests": [],
    }
