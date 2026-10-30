# 임베딩 모델 설정, 호출 결과와 카드별 벡터의 내부 데이터 계약을 정의한다.

import math
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.documents.pipeline.embedding_texts import EmbeddingView
from app.domains.documents.pipeline.models import NonBlankText


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, *, retryable=False, status_code=None, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after


class EmbeddingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # 공통 계약은 다른 모델도 표현하고, 공급자별 제약은 해당 클라이언트가 검사한다.
    provider: NonBlankText = "kure"
    model: NonBlankText = "kure-v3-20261016"
    dimensions: int = Field(default=2048, gt=0, strict=True)


class EmbeddingUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prompt_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)


class EmbeddingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: NonBlankText
    dimensions: int = Field(gt=0, strict=True)
    vectors: list[list[float]] = Field(min_length=1, max_length=8)
    usage: EmbeddingUsage

    @model_validator(mode="before")
    @classmethod
    def validate_vectors(cls, data):
        if not isinstance(data, dict):
            return data
        for vector in data.get("vectors", []):
            if not isinstance(vector, list) or len(vector) != data.get("dimensions"):
                raise ValueError("embedding dimension mismatch")
            try:
                valid = all(
                    type(value) in (int, float) and math.isfinite(value) for value in vector
                )
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("embedding values must be finite numbers")
            if not any(value != 0 for value in vector):
                raise ValueError("zero vectors are not valid embeddings")
        return data


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class CardEmbeddings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    experience_id: NonBlankText
    template_version: NonBlankText
    provider: NonBlankText
    model: NonBlankText
    dimensions: int
    input_type: Literal["document"] = "document"
    vectors: dict[EmbeddingView, list[float]]
    input_hashes: dict[EmbeddingView, NonBlankText]

    @model_validator(mode="after")
    def validate_view_mapping(self) -> Self:
        if not self.vectors or self.vectors.keys() != self.input_hashes.keys():
            raise ValueError("each generated vector must retain its input hash")
        return self
