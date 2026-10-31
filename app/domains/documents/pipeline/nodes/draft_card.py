# 노드: extract가 뽑은 경험(ExtractedElements)마다 카드 초안(CardDraft)을 만든다.
#
# 경험마다 개별 LLM 호출을 한다(배치 아님) — extract와 달리 카드 하나를 채우는 데 다른 카드의
# 내용을 볼 필요가 없어서, 호출을 분리하는 쪽이 결과 품질이 더 좋다.
# 구조적 유효성 검사(필수 필드/근거 1개 이상)는 하지 않는다 — verify로 위임.
import logging
import re
from datetime import date
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.technology import normalize_technologies
from app.domains.documents.pipeline.chunk_formatting import format_chunks
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import (
    CardDraft,
    CardDraftContent,
    CardEvidenceDraft,
    ExtractedElements,
)
from app.domains.documents.pipeline.stage import finish_stage
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "draft_card.md").read_text(encoding="utf-8")


def _experience_text(element: ExtractedElements) -> str:
    # null/빈 값을 "None"으로 찍으면 LLM이 실제 값처럼 다룰 수 있어서 "(없음)"으로 표시한다
    fields = {
        "problem": element.problem,
        "cause": element.cause,
        "approach": element.approach,
        "rationale": element.rationale,
        "outcome": element.outcome,
    }
    return "\n".join(
        f"{name}: {value.strip() if value and value.strip() else '(없음)'}"
        for name, value in fields.items()
    )


def _build_evidences(
    content: CardDraftContent, source_chunks: list[Chunk]
) -> tuple[list[CardEvidenceDraft], int, bool]:
    # (근거 목록, excerpt가 원문과 안 맞아 청크 전체로 폴백된 개수, 근거가 없어 소스 청크 전체로
    # 폴백했는지)를 돌려준다. 뒤의 둘은 로그용이다.
    # LLM이 이 경험의 소스 청크가 아닌 chunk_id를 고른 경우는 무시한다.
    source_by_id = {c["id"]: c for c in source_chunks}
    evidences: list[CardEvidenceDraft] = []
    seen: set[tuple[str, str]] = set()
    excerpt_fallbacks = 0
    for pick in content.excerpts:
        chunk = source_by_id.get(pick.chunk_id)
        if chunk is None:
            continue  # LLM이 존재하지 않거나 소스가 아닌 chunk_id를 고른 경우
        # "원문에서 그대로 가져온 근거"여야 하므로, 비었거나 LLM이 의역/변형했으면 원문 그대로로
        # 폴백한다. ("" in text는 항상 참이라 빈 excerpt를 따로 걸러야 한다)
        excerpt = pick.excerpt.strip()
        if not excerpt or excerpt not in chunk["text"]:
            excerpt = chunk["text"]
            excerpt_fallbacks += 1
        if (chunk["id"], excerpt) in seen:
            continue
        seen.add((chunk["id"], excerpt))
        evidences.append(_evidence(chunk, excerpt))

    # 근거가 하나도 없으면 카드가 verify의 구조 검사에서 통째로 폐기되므로
    # 소스 청크 전체를 근거로 쓴다
    if not evidences:
        return [_evidence(c, c["text"]) for c in source_chunks], excerpt_fallbacks, True
    return evidences, excerpt_fallbacks, False


_FULL_DATE_RE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?")


def _parse_full_date(value: str | None, source_text: str) -> date | None:
    # LLM이 날짜를 지어내지 못하게: 원문에 실제로 있는 표현이고, 연-월-일이 모두 있을 때만 인정한다
    if not value or value.strip() not in source_text:
        return None
    match = _FULL_DATE_RE.fullmatch(value.strip())
    if not match:
        return None
    try:
        return date(*(int(g) for g in match.groups()))
    except ValueError:
        return None


def _evidence(chunk: Chunk, excerpt: str) -> CardEvidenceDraft:
    return CardEvidenceDraft(
        document_id=chunk["document_id"], location=chunk["location"] or None, excerpt=excerpt
    )


def _label(element: ExtractedElements) -> str:
    # 로그/경고에서 어떤 경험인지 알아볼 수 있게 가장 앞선 비어있지 않은 요소를 짧게 쓴다
    text = next((v for v in (element.problem, element.approach, element.cause) if v), "")
    return text.strip()[:40] or "내용 없음"


def draft_card(state: IngestionState) -> dict:
    chunks_by_id = {c["id"]: c for c in state["chunks"]}
    llm = get_llm().with_structured_output(CardDraftContent)

    card_drafts: list[CardDraft] = []
    failures: list[str] = []
    excerpt_fallbacks = evidence_fallback_cards = 0
    for element in state["extracted_elements"]:
        source_chunks = [
            chunks_by_id[cid] for cid in element.source_chunk_ids if cid in chunks_by_id
        ]
        if not source_chunks:
            continue  # source_chunk_ids가 전부 무효하면(흔치 않음) 이 경험은 건너뛴다

        # 지시문은 시스템 메시지, 경험/원본 청크는 사용자 메시지로 분리해서
        # 본문 속 문장이 지시문과 섞이지 않게 한다
        user_message = (
            f"## 경험\n\n{_experience_text(element)}\n\n"
            f"## 원본 청크\n\n{format_chunks(source_chunks)}"
        )
        try:
            content = invoke_with_retry(llm, [SystemMessage(_PROMPT), HumanMessage(user_message)])
        except Exception as e:
            # 경험 하나가 실패해도 나머지는 계속 처리한다(stage.finish_stage 참고)
            logger.warning("draft_card: 경험 건너뜀 (%s): %s", _label(element), e)
            failures.append(f"카드 초안 생성 실패 ({_label(element)}): {e}")
            continue

        evidences, fallbacks, used_all_chunks = _build_evidences(content, source_chunks)
        excerpt_fallbacks += fallbacks
        evidence_fallback_cards += used_all_chunks

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
                technologies=normalize_technologies(content.technologies),
                keywords=content.keywords,
                project_name=content.project_name,
                department=content.department,
                project_date=_parse_full_date(
                    content.project_date, "\n".join(c["text"] for c in source_chunks)
                ),
                evidences=evidences,
            )
        )

    logger.info(
        "draft_card: 경험 %d개 -> 카드 %d개 (excerpt 폴백 %d건, 근거 전체 폴백 카드 %d개)",
        len(state["extracted_elements"]),
        len(card_drafts),
        excerpt_fallbacks,
        evidence_fallback_cards,
    )
    return finish_stage(state, "card_drafts", card_drafts, failures)
