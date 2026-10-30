# 노드: 최종 Verification Gate를 통과한 초안의 관점별 임베딩 입력 텍스트를 준비한다.

from app.domains.documents.pipeline.embedding_texts import (
    EmbeddingTextOptions,
)
from app.domains.documents.pipeline.embedding_texts import (
    build_embedding_texts as build_card_embedding_texts,
)
from app.domains.documents.pipeline.state import IngestionState


def build_embedding_texts(state: IngestionState) -> dict:
    results = state.get("verification_results", [])
    decisions = {item.experience_id: item.decision for item in results}
    drafts = state.get("verified_drafts", [])
    if len(decisions) != len(results):
        raise ValueError("embedding texts require unique final verification results")
    if len({item.experience_id for item in drafts}) != len(drafts):
        raise ValueError("embedding texts require unique admitted experiences")
    if any(decisions.get(item.experience_id) != "pass" for item in drafts):
        raise ValueError("embedding texts require a matching final Gate pass")
    options = EmbeddingTextOptions.model_validate(
        state.get("embedding_text_options", EmbeddingTextOptions())
    )
    # Replace, never append: rerunning the node must not duplicate or retain old views.
    return {
        "embedding_texts": [build_card_embedding_texts(item, options=options) for item in drafts]
    }
