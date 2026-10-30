# 관점별 입력을 최대 8개씩 호출하고 경험·관점별 벡터와 입력 해시를 연결한다.

from hashlib import sha256

from app.domains.documents.pipeline.embedding_texts import CardEmbeddingTexts
from app.domains.documents.pipeline.embeddings.models import (
    CardEmbeddings,
    EmbeddingBatch,
    EmbeddingClient,
    EmbeddingError,
    EmbeddingSpec,
)


async def embed_plans(
    plans: list[CardEmbeddingTexts],
    client: EmbeddingClient,
    *,
    spec: EmbeddingSpec,
    batch_size: int,
) -> dict:
    if type(batch_size) is not int or not 1 <= batch_size <= 8:
        raise EmbeddingError("EMBEDDING_INVALID_BATCH_SIZE")
    work = [(plan, view, text) for plan in plans for view, text in plan.texts.items()]
    cards, usage = {}, []
    for start in range(0, len(work), batch_size):
        batch_work = work[start : start + batch_size]
        result = await client.embed([text.text for _, _, text in batch_work])
        result = EmbeddingBatch.model_validate(result.model_dump())
        if result.model != spec.model or result.dimensions != spec.dimensions:
            raise EmbeddingError("EMBEDDING_MODEL_MISMATCH")
        if len(result.vectors) != len(batch_work):
            raise EmbeddingError("EMBEDDING_INVALID_OUTPUT")
        usage.append(result.usage)
        for (plan, view, text), vector in zip(batch_work, result.vectors, strict=True):
            if plan.experience_id not in cards:
                cards[plan.experience_id] = {
                    "experience_id": plan.experience_id,
                    "template_version": plan.template_version,
                    "provider": spec.provider,
                    "model": spec.model,
                    "dimensions": spec.dimensions,
                    "vectors": {},
                    "input_hashes": {},
                }
            card = cards[plan.experience_id]
            card["vectors"][view] = vector
            card["input_hashes"][view] = sha256(text.text.encode("utf-8")).hexdigest()
    # 모든 호출이 성공한 뒤에만 State에 반영할 결과를 반환한다.
    return {
        "embeddings": [CardEmbeddings(**card) for card in cards.values()],
        "embedding_usage": usage,
    }
