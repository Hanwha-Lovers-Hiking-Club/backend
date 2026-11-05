# 공식 KURE 임베딩 API를 호출하고 응답의 모델·인덱스·차원·벡터 값을 검증한다.

import math
import re

import httpx2 as httpx
from pydantic import ValidationError

from app.domains.documents.pipeline.embeddings.models import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingSpec,
    EmbeddingUsage,
)

KURE_EMBEDDINGS_URL = "https://kure.kullm.ai/api/v1/embeddings"


def validate_kure_key(key) -> str:
    value = key.get_secret_value().strip() if key is not None else ""
    if not value or not value.isascii() or any(char.isspace() for char in value):
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED")
    return value


class KureEmbeddingClient:
    def __init__(self, client: httpx.AsyncClient, spec: EmbeddingSpec, api_key: str):
        if (
            spec.provider != "kure"
            or not re.fullmatch(r"kure-v3-[0-9]{8}", spec.model)
            or spec.dimensions not in (256, 512, 768, 1024, 2048)
        ):
            raise EmbeddingError("EMBEDDING_INVALID_MODEL_SETTINGS")
        self.client, self.spec, self._api_key = client, spec, api_key

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= 8
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise EmbeddingError("EMBEDDING_INVALID_INPUT")
        try:
            response = await self.client.post(
                KURE_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.spec.model,
                    "input": texts,
                    "input_type": "document",
                    "dimensions": self.spec.dimensions,
                    "encoding_format": "float",
                },
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise EmbeddingError("EMBEDDING_TIMEOUT", retryable=True) from None
        except httpx.RequestError:
            raise EmbeddingError("EMBEDDING_CONNECTION_ERROR", retryable=True) from None
        if not response.is_success:
            raise self._http_error(response)
        try:
            body = response.json()
            if not isinstance(body, dict) or body.get("object") != "list":
                raise ValueError("expected embedding list")
            if body.get("model") != self.spec.model:
                raise EmbeddingError("EMBEDDING_MODEL_MISMATCH")
            rows = body["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("unexpected embedding count")
            indices = [row["index"] for row in rows]
            if any(type(index) is not int for index in indices) or sorted(indices) != list(
                range(len(texts))
            ):
                raise ValueError("invalid embedding indices")
            if any(row.get("object") != "embedding" for row in rows):
                raise ValueError("invalid embedding object")
            return EmbeddingBatch(
                model=body["model"],
                dimensions=self.spec.dimensions,
                vectors=[row["embedding"] for row in sorted(rows, key=lambda row: row["index"])],
                usage=EmbeddingUsage.model_validate(
                    {
                        "prompt_tokens": body["usage"]["prompt_tokens"],
                        "total_tokens": body["usage"]["total_tokens"],
                    }
                ),
            )
        except (ValueError, KeyError, TypeError, AttributeError, ValidationError):
            raise EmbeddingError("EMBEDDING_INVALID_OUTPUT") from None

    @staticmethod
    def _http_error(response) -> EmbeddingError:
        status = response.status_code
        code = {
            401: "EMBEDDING_AUTH_ERROR",
            403: "EMBEDDING_AUTH_ERROR",
            404: "EMBEDDING_MODEL_UNAVAILABLE",
            410: "EMBEDDING_MODEL_UNAVAILABLE",
            429: "EMBEDDING_RATE_LIMITED",
        }.get(status, "EMBEDDING_API_ERROR")
        if status == 400:
            try:
                if response.json().get("error", {}).get("code") == "context_length_exceeded":
                    code = "EMBEDDING_INPUT_TOO_LARGE"
            except (ValueError, AttributeError):
                pass
        try:
            retry_after = float(response.headers.get("retry-after", ""))
            if not math.isfinite(retry_after) or retry_after < 0:
                retry_after = None
        except ValueError:
            retry_after = None
        # 원문 오류 메시지·요청·키를 외부 오류에 넣지 않으며 자동 재시도하지 않는다.
        return EmbeddingError(
            code,
            retryable=status == 429 or status >= 500,
            status_code=status,
            retry_after=retry_after,
        )
