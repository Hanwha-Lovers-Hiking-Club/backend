# 카드 필드를 정해진 규칙으로 조합해 관점별 임베딩 입력을 만든다.
# 모델 호출, 원문 재작성, 벡터 저장은 수행하지 않는다.

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.documents.pipeline.models import KnowledgeCardDraft, NonBlankText
from app.domains.documents.pipeline.verification.models import VerificationResult

EmbeddingView = Literal["content", "situation", "solution", "lesson"]
TEMPLATE_VERSION = "card-views-v1"

# The field combinations follow the design. Minimum requirements are the local v1
# policy documented in memo.md, not a new database/search service contract.
VIEW_FIELDS: dict[EmbeddingView, tuple[str, ...]] = {
    "content": (
        "title",
        "summary",
        "context",
        "problem",
        "cause",
        "approach",
        "rationale",
        "outcome",
        "lesson",
    ),
    "situation": ("context", "problem"),
    "solution": ("problem", "cause", "approach", "rationale", "outcome"),
    "lesson": ("context", "problem", "lesson"),
}
REQUIRED_FIELDS: dict[EmbeddingView, tuple[str, ...]] = {
    "content": ("title", "summary", "context", "lesson"),
    "situation": ("context", "problem"),
    "solution": ("problem", "approach"),
    "lesson": ("context", "problem", "lesson"),
}
CONTENT_EXPERIENCE_FIELDS = ("problem", "cause", "approach", "rationale", "outcome")


class EmbeddingTextOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Optional character guard, NOT a provider token limit. Never silently truncate.
    # The embedding client must enforce its own tokenizer/model limit in the next step.
    max_chars: int | None = Field(default=None, gt=0, strict=True)


class EmbeddingText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: NonBlankText
    included_fields: tuple[NonBlankText, ...]


class EmbeddingTextSkip(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["INSUFFICIENT_FIELDS", "INPUT_TOO_LARGE"]
    missing_fields: tuple[NonBlankText, ...] = ()
    required_any_of: tuple[NonBlankText, ...] = ()
    text_chars: int | None = None


class CardEmbeddingTexts(BaseModel):
    """Up to four ready texts and explicit skips, linked to an unsaved experience."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experience_id: NonBlankText
    template_version: Literal["card-views-v1"] = TEMPLATE_VERSION
    texts: dict[EmbeddingView, EmbeddingText]
    skipped: dict[EmbeddingView, EmbeddingTextSkip]


def build_embedding_texts(
    draft: KnowledgeCardDraft, *, options: EmbeddingTextOptions | None = None
) -> CardEmbeddingTexts:
    """Build views from a card; the graph node enforces final Gate admission.

    Revalidate mutable draft instances. Preserve every included value byte-for-byte
    as Python text, including whitespace/code/logs. No metadata is inserted into text.
    This function does not perform semantic verification or activate/save a card.
    """
    draft = KnowledgeCardDraft.model_validate(draft.model_dump())
    options = EmbeddingTextOptions.model_validate(options or EmbeddingTextOptions())
    texts, skipped = {}, {}
    for view, fields in VIEW_FIELDS.items():
        included = tuple(name for name in fields if getattr(draft, name) is not None)
        missing = tuple(name for name in REQUIRED_FIELDS[view] if name not in included)
        any_of = (
            CONTENT_EXPERIENCE_FIELDS
            if view == "content" and not set(included).intersection(CONTENT_EXPERIENCE_FIELDS)
            else ()
        )
        if missing or any_of:
            skipped[view] = EmbeddingTextSkip(
                code="INSUFFICIENT_FIELDS", missing_fields=missing, required_any_of=any_of
            )
            continue
        text = "\n\n".join(f"[{name}]\n{getattr(draft, name)}" for name in included)
        if options.max_chars is not None and len(text) > options.max_chars:
            skipped[view] = EmbeddingTextSkip(code="INPUT_TOO_LARGE", text_chars=len(text))
            continue
        texts[view] = EmbeddingText(text=text, included_fields=included)
    return CardEmbeddingTexts(experience_id=draft.experience_id, texts=texts, skipped=skipped)


def build_admitted_embedding_texts(
    drafts: list[KnowledgeCardDraft],
    results: list[VerificationResult],
    *,
    options: EmbeddingTextOptions,
) -> list[CardEmbeddingTexts]:
    decisions = {item.experience_id: item.decision for item in results}
    if len(decisions) != len(results):
        raise ValueError("embedding texts require unique final verification results")
    if len({item.experience_id for item in drafts}) != len(drafts):
        raise ValueError("embedding texts require unique admitted experiences")
    if any(decisions.get(item.experience_id) != "pass" for item in drafts):
        raise ValueError("embedding texts require a matching final Gate pass")
    return [build_embedding_texts(item, options=options) for item in drafts]
