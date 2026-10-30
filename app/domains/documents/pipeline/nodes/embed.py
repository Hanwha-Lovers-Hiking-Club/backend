# 노드: 최종 검증 카드의 최신 입력만 KURE로 임베딩하고 결과를 State에 반환한다.
# 실제 카드·근거·벡터의 DB 저장은 후속 save 노드가 담당한다.

from app.domains.documents.pipeline.embedding_texts import (
    CardEmbeddingTexts,
    EmbeddingTextOptions,
    build_admitted_embedding_texts,
)
from app.domains.documents.pipeline.embeddings.models import EmbeddingError, EmbeddingSpec
from app.domains.documents.pipeline.embeddings.service import embed_plans
from app.domains.documents.pipeline.llm import configured_embedder


def make_embed_node(client=None, *, spec=None, batch_size=None):
    if (client is None) != (spec is None):
        raise ValueError("inject an embedding client and its model spec together")

    async def embed(state):
        from app.config import settings

        options = EmbeddingTextOptions.model_validate(
            state.get("embedding_text_options", EmbeddingTextOptions())
        )
        expected = build_admitted_embedding_texts(
            state.get("verified_drafts", []), state.get("verification_results", []), options=options
        )
        plans = [
            CardEmbeddingTexts.model_validate(
                item.model_dump() if isinstance(item, CardEmbeddingTexts) else item
            )
            for item in state.get("embedding_texts", [])
        ]
        if plans != expected:
            raise EmbeddingError("EMBEDDING_STALE_INPUT")
        if not any(plan.texts for plan in plans):
            return {"embeddings": [], "embedding_usage": []}
        model_spec = spec or EmbeddingSpec(
            model=settings.EMBEDDING_MODEL, dimensions=settings.EMBEDDING_DIMENSIONS
        )
        size = settings.EMBEDDING_BATCH_SIZE if batch_size is None else batch_size
        if client is not None:
            return await embed_plans(plans, client, spec=model_spec, batch_size=size)
        async with configured_embedder() as configured:
            return await embed_plans(plans, configured, spec=model_spec, batch_size=size)

    return embed


embed = make_embed_node()
