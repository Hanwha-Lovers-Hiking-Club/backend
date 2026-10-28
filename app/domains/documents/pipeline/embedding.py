# kure-v3 임베딩 클라이언트 생성을 한 곳에 모은다 (모델/차원 등 설정 관리).
# input_type을 항상 "document"로 고정하는 이유: 이 함수는 지식카드를 "저장"할 때 쓰는 용도다.
# 반대로 사용자 질문을 임베딩할 때는 "query"를 써야 하는데, 그건 챗봇 도메인의 일이라 여기서
# 다루지 않는다.
from functools import lru_cache

from openai import OpenAI

from app.config import settings

EMBEDDING_MODEL_NAME = "kure-v3"
# pgvector 인덱스 상한선(2,000차원) 대비 여유 있게 잡은 값. docs/schema.md 참고
EMBEDDING_DIMENSIONS = 1024


@lru_cache
def get_embedding_client() -> OpenAI:
    return OpenAI(base_url="https://kure.kullm.ai/api/v1", api_key=settings.KURE_API_KEY)


def embed_documents(texts: list[str]) -> list[list[float]]:
    # kure-v3는 input에 리스트를 받아서 한 번에 임베딩해준다. 서로 독립적인 텍스트들을 묶어서
    # 보낼 때(예: 카드 1개의 4종 임베딩용 텍스트) 왕복 횟수를 줄이는 용도
    response = get_embedding_client().embeddings.create(
        model=EMBEDDING_MODEL_NAME,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
        extra_body={"input_type": "document"},
    )
    return [item.embedding for item in response.data]


def embed_document(text: str) -> list[float]:
    return embed_documents([text])[0]
