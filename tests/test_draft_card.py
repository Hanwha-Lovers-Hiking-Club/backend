from datetime import date
from uuid import uuid4

from app.domains.documents.pipeline.models import (
    CardComposition,
    Evidence,
    Experience,
    OptionalSourcedText,
    SourcedText,
)
from app.domains.documents.pipeline.nodes import draft_card as DC

DOC1, DOC2 = uuid4(), uuid4()
HEADER = "정산 대응 기록\n프로젝트: 로컬픽 정산\n부서: 정산플랫폼팀\n작성 2026-08-24"
TEXTS = {
    (DOC1, 0): HEADER,
    (DOC1, 1): "2026-08-12 두 pod가 배치를 각각 실행해 312건이 중복 지급됐다.",
    (DOC2, 0): "재발 방지 회의록",
    (DOC2, 1): "DB 스케줄러 락을 채택했다. 배포 후 5회 배치에서 중복 0건.",
}


def _state(*experiences) -> dict:
    return {
        "documents": [
            {
                "id": DOC1,
                "storage_bucket": "b",
                "storage_path": "p",
                "original_filename": "대응.txt",
            },
            {
                "id": DOC2,
                "storage_bucket": "b",
                "storage_path": "p",
                "original_filename": "회의.docx",
            },
        ],
        "chunks": [
            {"id": f"{d}:{i}", "document_id": d, "index": i, "text": t, "location": f"절{i}"}
            for (d, i), t in TEXTS.items()
        ],
        "experiences": list(experiences),
    }


def _experience(title="중복 지급") -> Experience:
    return Experience(
        title=title,
        summary="s",
        link_hints=[],
        evidences=[
            Evidence(
                kind="observation",
                statement="중복 지급",
                chunk_id=f"{DOC1}:1",
                quote="2026-08-12 두 pod가 배치를 각각 실행해 312건이 중복 지급됐다.",
            ),
            Evidence(
                kind="decision",
                statement="락 채택",
                chunk_id=f"{DOC2}:1",
                quote="DB 스케줄러 락을 채택했다.",
            ),
            Evidence(
                kind="result",
                statement="중복 0건",
                chunk_id=f"{DOC2}:1",
                quote="배포 후 5회 배치에서 중복 0건.",
            ),
        ],
    )


def _s(ids, text):
    return SourcedText(source_ids=ids, text=text)


def _o(ids=(), text=None):
    return OptionalSourcedText(source_ids=list(ids), text=text)


def _composition(**overrides) -> CardComposition:
    fields = dict(
        lesson_supportable=True,
        lesson_reason="결과 근거 있음",
        title=_s(["E1"], "배치 중복 실행"),
        summary=_s(["E1", "E2"], "요약"),
        context=_s(["E1"], "배경"),
        problem=_o(["E1"], "중복 지급"),
        cause=_o(),
        approach=_o(["E2"], "스케줄러 락"),
        rationale=_o(),
        outcome=_o(["E3", "E1"], "배포 후 5회 배치에서 중복 0건"),
        lesson=_s(["E2", "E3"], "교훈"),
        technologies=[],
        keywords=["데이터 정합성"],
        project_name=_o(["H1"], "로컬픽 정산"),
        department=_o(["H1"], "재무팀"),  # 머리말에 없는 값
        project_date=_o(["E1"], "2026-08-12"),
    )
    return CardComposition(**{**fields, **overrides})


class _LLM:
    def __init__(self, results: dict):
        # 경험 제목 -> CardComposition 또는 예외
        self.results, self.messages = results, []

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages.append(messages[1].content)
        title = messages[1].content.split("제목: ")[1].split("\n")[0]
        result = self.results[title]
        if isinstance(result, Exception):
            raise result
        return result


def _run(monkeypatch, results: dict, *experiences) -> tuple[dict, _LLM]:
    llm = _LLM(results)
    monkeypatch.setattr(DC, "get_llm", lambda: llm)
    return DC.draft_card(_state(*experiences)), llm


def test_card_cites_only_given_sources_and_keeps_field_level_mapping(monkeypatch):
    result, llm = _run(monkeypatch, {"중복 지급": _composition()}, _experience())
    [draft] = result["card_drafts"]
    message = llm.messages[0]
    assert "E3 [result] (회의.docx · 절1)" in message and "H1 (대응.txt)" in message
    assert "재발 방지 회의록" in message  # 근거가 나온 문서의 머리말
    # 같은 근거를 여러 필드가 인용해도 근거 행은 한 번만 만든다. 문서 두 개가 모두 근거로 남는다
    excerpts = [e.excerpt for e in draft.evidences]
    assert len(excerpts) == len(set(excerpts)) and {e.document_id for e in draft.evidences} == {
        DOC1,
        DOC2,
    }
    outcome = [draft.evidences[i].excerpt for i in draft.field_sources["outcome"]]
    assert outcome == [
        "배포 후 5회 배치에서 중복 0건.",
        "2026-08-12 두 pod가 배치를 각각 실행해 312건이 중복 지급됐다.",
    ]
    # 머리말에서 온 값은 그 값이 있는 줄을 근거로 남기고, 머리말에 없는 값은 비운다
    assert draft.project_name == "로컬픽 정산" and draft.department is None
    assert (
        draft.evidences[draft.field_sources["project_name"][0]].excerpt == "프로젝트: 로컬픽 정산"
    )
    assert draft.project_date == date(2026, 8, 12)
    assert result["draft_skips"] == []


def test_outcome_without_result_evidence_is_cleared(monkeypatch):
    composition = _composition(outcome=_o(["E2"], "락을 채택해 문제를 해결했다"))
    result, _ = _run(monkeypatch, {"중복 지급": composition}, _experience())
    draft = result["card_drafts"][0]
    assert draft.outcome is None and "outcome" not in draft.field_sources


def test_fields_citing_unknown_ids_count_as_unsourced(monkeypatch):
    composition = _composition(problem=_o(["E9"], "지어낸 문제"), context=_s(["E9"], "배경"))
    result, _ = _run(monkeypatch, {"중복 지급": composition}, _experience())
    assert result["card_drafts"] == []
    assert "context" in result["draft_skips"][0]


def test_lesson_not_supportable_is_a_skip_with_reason_not_a_failure(monkeypatch):
    skip = _composition(lesson_supportable=False, lesson_reason="계획만 있음")
    result, _ = _run(
        monkeypatch, {"a": skip, "b": _composition()}, _experience("a"), _experience("b")
    )
    assert len(result["card_drafts"]) == 1 and "error" not in result and not result.get("warnings")
    assert result["draft_skips"] == ["a: lesson을 뒷받침할 근거 부족 — 계획만 있음"]


def test_all_conditional_fields_empty_is_skipped(monkeypatch):
    empty = {f: _o() for f in ("problem", "cause", "approach", "rationale", "outcome")}
    result, _ = _run(monkeypatch, {"중복 지급": _composition(**empty)}, _experience())
    assert result["card_drafts"] == [] and "조건부" in result["draft_skips"][0]


def test_date_not_in_cited_source_or_not_full_is_dropped(monkeypatch):
    composition = _composition(project_date=_o(["E2"], "2026-08-12"))  # E2 원문에 없는 날짜
    result, _ = _run(monkeypatch, {"중복 지급": composition}, _experience())
    assert result["card_drafts"][0].project_date is None


def test_one_failed_call_is_skipped_and_all_failed_is_an_error(monkeypatch):
    result, _ = _run(
        monkeypatch,
        {"a": RuntimeError("down"), "b": _composition()},
        _experience("a"),
        _experience("b"),
    )
    assert len(result["card_drafts"]) == 1 and "카드 후보 생성 실패 (a)" in result["warnings"][0]
    result, _ = _run(monkeypatch, {"a": RuntimeError("down")}, _experience("a"))
    assert result["error"].startswith("카드 후보 생성 실패")


def test_no_experiences_skips_the_llm(monkeypatch):
    monkeypatch.setattr(DC, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("호출 금지")))
    assert DC.draft_card(_state()) == {"card_drafts": [], "draft_skips": []}


def test_string_document_ids_from_the_database_work(monkeypatch):
    # 실제 업로드 경로에서는 문서 id가 DB가 돌려준 문자열이다(CardEvidenceDraft는 UUID로 바꾼다)
    llm = _LLM({"중복 지급": _composition()})
    monkeypatch.setattr(DC, "get_llm", lambda: llm)
    state = _state(_experience())
    for d in state["documents"]:
        d["id"] = str(d["id"])
    for c in state["chunks"]:
        c["document_id"] = str(c["document_id"])
    result = DC.draft_card(state)
    assert len(result["card_drafts"]) == 1 and "H1 (대응.txt)" in llm.messages[0]


def test_evidence_location_uses_the_precise_position_when_found(monkeypatch):
    llm = _LLM({"중복 지급": _composition()})
    monkeypatch.setattr(DC, "get_llm", lambda: llm)
    state = _state(_experience())
    state["positions"] = {
        str(DOC2): [
            {
                "text": "DB 스케줄러 락을 채택했다.",
                "location": "3. 결정 사항 · 단락 4",
                "heading": "절1",
            }
        ]
    }
    draft = DC.draft_card(state)["card_drafts"][0]
    locations = {e.excerpt: e.location for e in draft.evidences}
    assert locations["DB 스케줄러 락을 채택했다."] == "3. 결정 사항 · 단락 4"
    # 위치 목록에 없는 문서는 지금처럼 청크의 헤딩 경로를 쓴다
    assert locations["배포 후 5회 배치에서 중복 0건."] == "절1"
    assert "회의.docx · 절1" in llm.messages[0]  # 프롬프트 입력은 그대로(청크 위치)
