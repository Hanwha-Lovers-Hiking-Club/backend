"""카드 저장 서비스의 공개 dict 계약과 오류를 정의한다. 파이프라인 내부 타입은 사용하지 않는다."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strict=True, pattern=r"\S")]
PayloadHash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
Vector = list[Annotated[float, Field(strict=True, allow_inf_nan=False)]]
VECTOR_COLUMNS = (
    "content_embedding",
    "situation_embedding",
    "solution_embedding",
    "lesson_embedding",
)


class CardPersistenceError(RuntimeError):
    """저장이 확정되지 않았다. 호출자는 성공 영수증을 만들거나 임시 근거를 지우면 안 된다."""


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceWrite(WriteModel):
    document_id: UUID
    location: Text
    excerpt: Text


class CardWrite(WriteModel):
    title: Text
    summary: Text
    context: Text
    problem: Text | None = None
    cause: Text | None = None
    approach: Text | None = None
    rationale: Text | None = None
    outcome: Text | None = None
    lesson: Text
    technologies: list[Text]
    keywords: list[Text]
    status: Literal["active"]
    evidences: list[EvidenceWrite] = Field(min_length=1)
    content_embedding: Vector | None = None
    situation_embedding: Vector | None = None
    solution_embedding: Vector | None = None
    lesson_embedding: Vector | None = None

    @model_validator(mode="after")
    def validate_minimum_content(self) -> Self:
        if not any((self.problem, self.cause, self.approach, self.rationale, self.outcome)):
            raise ValueError("card requires at least one experience field")
        return self


class DocumentCardsWrite(WriteModel):
    document_id: UUID
    payload_hash: PayloadHash
    cards: list[CardWrite]

    @model_validator(mode="after")
    def validate_document_scope(self) -> Self:
        if any(
            ref.document_id != self.document_id for card in self.cards for ref in card.evidences
        ):
            raise ValueError("evidence belongs to a different document")
        return self


class DocumentCardsReceipt(WriteModel):
    # 요청한 pass 카드 집합의 저장 확인이다. 문서의 보류 해소를 의미하지 않는다.
    document_id: UUID
    payload_hash: PayloadHash
    committed: Literal[True]
    result_recorded: Literal[True]
    card_ids: list[UUID]
