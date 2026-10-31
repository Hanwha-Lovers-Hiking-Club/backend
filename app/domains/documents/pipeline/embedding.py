# kure-v3 임베딩 클라이언트 생성을 한 곳에 모은다 (모델/차원 등 설정 관리).
# input_type을 항상 "document"로 고정하는 이유: 이 함수는 지식카드를 "저장"할 때 쓰는 용도다.
# 반대로 사용자 질문을 임베딩할 때는 "query"를 써야 하는데, 그건 챗봇 도메인의 일이라 여기서
# 다루지 않는다.
import time
from functools import lru_cache

from openai import OpenAI

from app.config import settings
from app.domains.documents.pipeline.observe import record_embedding

EMBEDDING_MODEL_NAME = "kure-v3"
# pgvector 인덱스 상한선(2,000차원) 대비 여유 있게 잡은 값. docs/schema.md 참고
EMBEDDING_DIMENSIONS = 1024
# 일시적 오류(연결 오류, 408/409/429/5xx)는 OpenAI SDK가 0.5초~8초 지수 백오프로 알아서 재시도한다.
# SDK 기본값(2)과 같지만 정책으로 드러내려고 명시했다. 카드(embed)와 청크(embed_chunks)가 같이 쓴다
EMBEDDING_MAX_RETRIES = 2


@lru_cache
def get_embedding_client() -> OpenAI:
    return OpenAI(
        base_url="https://kure.kullm.ai/api/v1",
        api_key=settings.KURE_API_KEY,
        max_retries=EMBEDDING_MAX_RETRIES,
    )


def embed_documents(texts: list[str]) -> list[list[float]]:
    # kure-v3는 input에 리스트를 받아서 한 번에 임베딩해준다. 서로 독립적인 텍스트들을 묶어서
    # 보낼 때(예: 카드 1개의 4종 임베딩용 텍스트) 왕복 횟수를 줄이는 용도
    started = time.perf_counter()
    response = get_embedding_client().embeddings.create(
        model=EMBEDDING_MODEL_NAME,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
        extra_body={"input_type": "document"},
    )
    # kure가 usage를 주는지 확인 안 됨: 없으면 토큰은 로그에서 생략한다
    usage = getattr(response, "usage", None)
    record_embedding(
        len(texts), time.perf_counter() - started, getattr(usage, "total_tokens", None)
    )
    # 개수/차원이 어긋나면 저장 시점의 모호한 DB 에러가 되기 전에 여기서 원인이 분명하게 실패시킨다
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts):
        raise ValueError(f"임베딩 개수 불일치: 입력 {len(texts)}개, 결과 {len(vectors)}개")
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"임베딩 차원 불일치: 기대 {EMBEDDING_DIMENSIONS}, 실제 {len(vector)}")
    return vectors


def embed_document(text: str) -> list[float]:
    return embed_documents([text])[0]
