# 노드: extract가 뽑은 경험마다 지식카드 후보(CardDraft)를 만든다.
#
# 책임은 "검증할 수 있는 후보"를 만드는 것까지다. 카드의 신뢰성과 저장 상태(active/draft)는
# 뒤의 검증 노드가 원문을 대조한 뒤 정한다. 여기서 버린 경험은 검증 노드가 볼 기회가 없으므로,
# 코드는 구조 문제만 잡는다: 공백, 없는 근거 id, 근거 없는 필수 필드, 조건부 필드 전부 비어 있음,
# result 근거 없는 outcome.
# 근거 종류(kind)로 신뢰성을 판정하지 않는다(종류는 검증 노드가 무엇을 볼지 알려 주는 단서다).
# LLM이 lesson을 근거로 쓸 수 없다고 판단한 경험은 후보를 만들지 않고 사유를 draft_skips에 남긴다.
#
# 입력은 그 경험의 근거(E1..)와 근거가 나온 문서의 첫 청크(머리말 H1..: 프로젝트명·부서 등)뿐이다.
# 원문 전체를 주지 않는 것은 근거 밖 내용이 카드에 들어오는 통로를 줄이기 위해서다.
# 경험끼리는 독립이라 병렬로 호출한다.
import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import summarize_error
from app.core.technology import normalize_technologies
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import (
    CardComposition,
    CardDraft,
    CardEvidenceDraft,
    Experience,
    OptionalSourcedText,
    SourcedText,
)
from app.domains.documents.pipeline.stage import finish_stage
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "draft_card.md").read_text(encoding="utf-8")

MAX_WORKERS = 4
# 머리말은 작성 정보·범위가 있는 앞부분만 준다
_HEADER_CHARS = 1_500
_REQUIRED = ("title", "summary", "context", "lesson")
_CONDITIONAL = ("problem", "cause", "approach", "rationale", "outcome")
_NON_TEXT = re.compile(r"[\W_]+")
_FULL_DATE_RE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?")


def _plain(text: str) -> str:
    return _NON_TEXT.sub("", text)


class _Sources:
    # 프롬프트에 넣은 근거(E)와 머리말(H). 카드가 인용한 것만 CardDraft.evidences로 만든다
    def __init__(self, experience: Experience, chunks: dict[str, Chunk], heads: dict, names: dict):
        self.evidences: dict[str, CardEvidenceDraft] = {}
        self.statements: dict[str, str] = {}
        for i, ev in enumerate((e for e in experience.evidences if e.chunk_id in chunks), 1):
            c = chunks[ev.chunk_id]
            self.statements[f"E{i}"] = ev.statement
            self.evidences[f"E{i}"] = CardEvidenceDraft(
                document_id=c["document_id"],
                location=c["location"] or None,
                excerpt=ev.quote,
                chunk_id=c["id"],
                kind=ev.kind,
            )
        documents = dict.fromkeys(e.document_id for e in self.evidences.values())
        self.headers: dict[str, Chunk] = {
            f"H{j}": heads[d] for j, d in enumerate(documents, 1) if d in heads
        }
        self.names = names
        self.experience = experience

    def message(self) -> str:
        e = self.experience
        lines = ["## 경험", f"제목: {e.title}", f"요약: {e.summary}", "", "## 근거"]
        for sid, ev in self.evidences.items():
            where = f"{self.names[ev.document_id]} · {ev.location or '위치 없음'}"
            lines += [
                f"{sid} [{ev.kind}] ({where})",
                f"  내용: {self.statements[sid]}",
                f"  원문: {ev.excerpt}",
            ]
        lines += ["", "## 문서 머리말"]
        for hid, c in self.headers.items():
            lines += [f"{hid} ({self.names[c['document_id']]})", c["text"][:_HEADER_CHARS], ""]
        return "\n".join(lines)

    def is_result(self, sid: str) -> bool:
        return sid in self.evidences and self.evidences[sid].kind == "result"

    def valid(self, ids: list[str]) -> list[str]:
        return [i for i in dict.fromkeys(ids) if i in self.evidences or i in self.headers]

    def text_of(self, sid: str) -> str:
        if sid in self.evidences:
            return self.evidences[sid].excerpt or ""
        return self.headers[sid]["text"][:_HEADER_CHARS]

    def evidence(self, sid: str, value: str | None = None) -> CardEvidenceDraft:
        if sid in self.evidences:
            return self.evidences[sid]
        # 머리말 근거: 값(프로젝트명 등)이 있는 줄을 발췌문으로 쓴다. 값이 없으면 머리말 앞부분
        c = self.headers[sid]
        text = c["text"][:_HEADER_CHARS]
        line = next((ln for ln in text.splitlines() if value and value in ln), None)
        return CardEvidenceDraft(
            document_id=c["document_id"],
            location=c["location"] or None,
            excerpt=(line or text).strip(),
            chunk_id=c["id"],
        )


def _text(field: SourcedText | OptionalSourcedText) -> str | None:
    return field.text.strip() if field.text and field.text.strip() else None


def _full_date(value: str) -> date | None:
    match = _FULL_DATE_RE.fullmatch(value)
    if not match:
        return None
    try:
        return date(*(int(g) for g in match.groups()))
    except ValueError:
        return None


def compose_one(
    experience: Experience, chunks: dict[str, Chunk], heads: dict, names: dict, llm
) -> tuple[CardDraft | None, str | None]:
    """경험 하나로 카드 후보를 만든다. (후보, None) 또는 (None, 후보를 만들지 않은 사유)를 돌려준다.

    LLM 호출이 실패하면 예외를 그대로 던진다(호출하는 쪽이 건너뛰기를 정한다).
    """
    label = experience.title.strip()[:40] or "제목 없음"
    sources = _Sources(experience, chunks, heads, names)
    if not sources.evidences:
        return None, f"{label}: 원문 청크를 찾을 수 없는 근거뿐"
    c: CardComposition = invoke_with_retry(
        llm, [SystemMessage(_PROMPT), HumanMessage(sources.message())]
    )
    if not c.lesson_supportable:
        return None, f"{label}: lesson을 뒷받침할 근거 부족 — {c.lesson_reason}"

    evidences: list[CardEvidenceDraft] = []
    position: dict[tuple, int] = {}
    field_sources: dict[str, list[int]] = {}
    values: dict[str, str | None] = {}

    def cite(name: str, ids: list[str], value: str | None = None) -> None:
        for sid in ids:
            ev = sources.evidence(sid, value)
            key = (ev.chunk_id, ev.excerpt)
            if key not in position:
                position[key] = len(evidences)
                evidences.append(ev)
            field_sources.setdefault(name, []).append(position[key])

    for name in (*_REQUIRED, *_CONDITIONAL):
        field = getattr(c, name)
        text, ids = _text(field), sources.valid(field.source_ids)
        if name == "outcome" and not any(sources.is_result(i) for i in ids):
            # 결과 근거 없이 쓴 outcome은 결과가 아니다(조치 전 관찰, 결정, 기대 효과 등)
            text = None
        if name in _REQUIRED and not (text and ids):
            return None, f"{label}: 필수 필드 {name}이(가) 비었거나 근거가 없음"
        values[name] = text if ids else None
        if values[name]:
            cite(name, ids)
    if not any(values[f] for f in _CONDITIONAL):
        return None, f"{label}: 조건부 필드(problem~outcome)가 모두 비어 있음"

    # 선택 필드는 인용한 근거의 원문에 그 값이 실제로 있을 때만 쓴다
    for name in ("project_name", "department", "project_date"):
        field = getattr(c, name)
        text, ids = _text(field), sources.valid(field.source_ids)
        grounded = [
            i for i in ids if text and _plain(text) and _plain(text) in _plain(sources.text_of(i))
        ]
        values[name] = text if grounded else None
        if values[name]:
            cite(name, grounded, text)

    project_date = _full_date(values["project_date"]) if values["project_date"] else None
    if project_date is None:
        field_sources.pop("project_date", None)
    draft = CardDraft(
        title=values["title"],
        summary=values["summary"],
        context=values["context"],
        problem=values["problem"],
        cause=values["cause"],
        approach=values["approach"],
        rationale=values["rationale"],
        outcome=values["outcome"],
        lesson=values["lesson"],
        technologies=normalize_technologies(c.technologies),
        keywords=[k.strip() for k in c.keywords if k.strip()],
        project_name=values["project_name"],
        department=values["department"],
        project_date=project_date,
        evidences=evidences,
        field_sources=field_sources,
    )
    return draft, None


def draft_card(state: IngestionState) -> dict:
    experiences = state.get("experiences", [])
    if not experiences:
        return {"card_drafts": [], "draft_skips": []}
    chunks = {c["id"]: c for c in state["chunks"]}
    heads = {c["document_id"]: c for c in state["chunks"] if c["index"] == 0}
    names = {d["id"]: d["original_filename"] for d in state["documents"]}
    llm = get_llm().with_structured_output(CardComposition)

    # 스레드에는 contextvar(job_id 로그·사용량 집계)가 넘어가지 않아서 호출마다 복사해 넘긴다
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, compose_one, e, chunks, heads, names, llm)
            for e in experiences
        ]

    drafts: list[CardDraft] = []
    skips: list[str] = []
    failures: list[str] = []
    for experience, future in zip(experiences, futures):
        try:
            draft, skip = future.result()
        except Exception as e:
            # 경험 하나가 실패해도 나머지는 계속 처리한다(stage.finish_stage 참고)
            label = experience.title.strip()[:40]
            logger.warning("draft_card: 경험 건너뜀 (%s): %s", label, summarize_error(e))
            failures.append(f"카드 후보 생성 실패 ({label}): {summarize_error(e)}")
            continue
        if draft:
            drafts.append(draft)
        else:
            logger.info("draft_card: 후보 미생성 %s", skip)
            skips.append(skip)

    logger.info(
        "draft_card: 경험 %d개 -> 후보 %d개, 미생성 %d개, 실패 %d개",
        len(experiences),
        len(drafts),
        len(skips),
        len(failures),
    )
    update = finish_stage(
        state, "card_drafts", drafts, failures, succeeded=len(drafts) + len(skips)
    )
    if "error" not in update:
        update["draft_skips"] = skips
    return update
