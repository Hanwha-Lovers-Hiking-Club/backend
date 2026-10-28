# 노드: extract가 뽑은 경험(ExtractedElements)마다 카드 초안(CardDraft)을 만든다.
#
# 경험마다 개별 LLM 호출을 한다(배치 아님) — extract와 달리 카드 하나를 채우는 데 다른 카드의
# 내용을 볼 필요가 없어서, 호출을 분리하는 쪽이 결과 품질이 더 좋다.
# 구조적 유효성 검사(필수 필드/근거 1개 이상)는 하지 않는다 — verify로 위임.
from pathlib import Path

from app.domains.documents.pipeline.chunk_formatting import format_chunks
from app.domains.documents.pipeline.llm import get_llm
from app.domains.documents.pipeline.models import CardDraft, CardDraftContent, CardEvidenceDraft
from app.domains.documents.pipeline.state import Chunk, IngestionState

_PROMPT = (Path(__file__).parent.parent / "prompts" / "draft_card.md").read_text(encoding="utf-8")


def _build_evidences(
    content: CardDraftContent, chunks_by_id: dict[str, Chunk]
) -> list[CardEvidenceDraft]:
    evidences: list[CardEvidenceDraft] = []
    for pick in content.excerpts:
        chunk = chunks_by_id.get(pick.chunk_id)
        if chunk is None:
            continue  # LLM이 존재하지 않는 chunk_id를 지어낸 경우
        # "원문에서 그대로 가져온 근거"여야 하므로, LLM이 의역/변형했으면 원문 그대로로
        # 폴백한다
        excerpt = pick.excerpt if pick.excerpt in chunk["text"] else chunk["text"]
        evidences.append(
            CardEvidenceDraft(
                document_id=chunk["document_id"],
                location=chunk["location"] or None,
                excerpt=excerpt,
            )
        )
    return evidences


def draft_card(state: IngestionState) -> dict:
    chunks_by_id = {c["id"]: c for c in state["chunks"]}
    llm = get_llm().with_structured_output(CardDraftContent)

    card_drafts: list[CardDraft] = []
    for element in state["extracted_elements"]:
        source_chunks = [
            chunks_by_id[cid] for cid in element.source_chunk_ids if cid in chunks_by_id
        ]
        if not source_chunks:
            continue  # source_chunk_ids가 전부 무효하면(흔치 않음) 이 경험은 건너뛴다

        experience_text = (
            f"problem: {element.problem}\n"
            f"cause: {element.cause}\n"
            f"approach: {element.approach}\n"
            f"rationale: {element.rationale}\n"
            f"outcome: {element.outcome}"
        )
        prompt = f"{_PROMPT}\n{experience_text}\n\n## 원본 청크\n\n{format_chunks(source_chunks)}"

        try:
            content = llm.invoke(prompt)
        except Exception as e:
            return {"error": f"카드 초안 생성 실패: {e}"}

        card_drafts.append(
            CardDraft(
                title=content.title,
                summary=content.summary,
                context=content.context,
                problem=element.problem,
                cause=element.cause,
                approach=element.approach,
                rationale=element.rationale,
                outcome=element.outcome,
                lesson=content.lesson,
                technologies=content.technologies,
                keywords=content.keywords,
                project_name=content.project_name,
                department=content.department,
                project_date=content.project_date,
                evidences=_build_evidences(content, chunks_by_id),
            )
        )

    return {"card_drafts": card_drafts}
