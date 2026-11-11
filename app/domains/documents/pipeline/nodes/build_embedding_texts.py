# 노드: 최종 Verification Gate를 통과한 초안의 관점별 임베딩 입력 텍스트를 준비한다.

from app.domains.documents.pipeline.embedding_texts import (
    EmbeddingTextOptions,
    build_admitted_embedding_texts,
)
from app.domains.documents.pipeline.state import IngestionState


def build_embedding_texts(state: IngestionState) -> dict:
    options = EmbeddingTextOptions.model_validate(
        state.get("embedding_text_options", EmbeddingTextOptions())
    )
    # Replace, never append: rerunning the node must not duplicate or retain old views.
    return {
        "embedding_texts": build_admitted_embedding_texts(
            state.get("verified_drafts", []), state.get("verification_results", []), options=options
        ),
        "embeddings": [],
        "embedding_usage": [],
    }
