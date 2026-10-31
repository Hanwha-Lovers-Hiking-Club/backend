from uuid import uuid4

from app.domains.documents.pipeline.models import (
    CardDraftContent,
    CardExcerptPick,
    ExtractedElements,
)
from app.domains.documents.pipeline.nodes import draft_card as D

DOC = uuid4()
CHUNKS = [
    {"id": "d:0", "document_id": DOC, "text": "첫째 문장. 둘째 문장.", "location": "1장"},
    {"id": "d:1", "document_id": DOC, "text": "다른 청크 본문", "location": ""},
    {"id": "d:2", "document_id": DOC, "text": "소스가 아닌 청크", "location": ""},
]
ELEMENT = ExtractedElements(
    problem="문제",
    cause=None,
    approach="해결",
    rationale=" ",
    outcome=None,
    source_chunk_ids=["d:0", "d:1"],
)


def _content(picks: list[tuple[str, str]], date_text: str | None = None) -> CardDraftContent:
    return CardDraftContent(
        title="t",
        summary="s",
        context="c",
        lesson="l",
        technologies=[],
        keywords=[],
        project_name=None,
        department=None,
        project_date=None,
        excerpts=[CardExcerptPick(chunk_id=i, excerpt=e) for i, e in picks],
    )


class _FakeLLM:
    def __init__(self, content):
        self.content, self.messages = content, None

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.content


def _run(monkeypatch, picks):
    fake = _FakeLLM(_content(picks))
    monkeypatch.setattr(D, "get_llm", lambda: fake)
    result = D.draft_card({"chunks": CHUNKS, "extracted_elements": [ELEMENT]})
    return result["card_drafts"][0].evidences, fake


def test_exact_excerpt_is_kept(monkeypatch):
    evidences, _ = _run(monkeypatch, [("d:0", "둘째 문장.")])
    assert [(e.excerpt, e.location) for e in evidences] == [("둘째 문장.", "1장")]


def test_empty_or_altered_excerpt_falls_back_to_chunk_text(monkeypatch):
    evidences, _ = _run(monkeypatch, [("d:0", "  "), ("d:1", "의역한 문장")])
    assert [e.excerpt for e in evidences] == ["첫째 문장. 둘째 문장.", "다른 청크 본문"]


def test_duplicate_picks_are_deduped(monkeypatch):
    evidences, _ = _run(monkeypatch, [("d:0", "첫째 문장."), ("d:0", "첫째 문장.")])
    assert len(evidences) == 1


def test_no_valid_picks_falls_back_to_all_source_chunks(monkeypatch):
    # d:2는 존재하지만 이 경험의 소스 청크가 아니라 무시된다
    evidences, _ = _run(monkeypatch, [("d:2", "소스가 아닌 청크"), ("x:9", "없음")])
    assert [e.excerpt for e in evidences] == ["첫째 문장. 둘째 문장.", "다른 청크 본문"]


def test_prompt_split_and_null_fields_shown_as_none_marker(monkeypatch):
    _, fake = _run(monkeypatch, [])
    system, human = fake.messages
    assert system.type == "system" and human.type == "human"
    assert "cause: (없음)" in human.content and "rationale: (없음)" in human.content
    assert "None" not in human.content and '<chunk id="d:0"' in human.content


def test_only_full_dates_in_source_are_kept():
    src = "2024-03-15 작성. 2024년 3월 말에 시작. 2024.4.2 배포"
    assert D._parse_full_date("2024-03-15", src).isoformat() == "2024-03-15"
    assert D._parse_full_date("2024.4.2", src).isoformat() == "2024-04-02"
    assert D._parse_full_date("2024년 3월", src) is None  # 일이 없음
    assert D._parse_full_date("2024-03-01", src) is None  # 원문에 없는 지어낸 날짜
    assert D._parse_full_date("2024-02-30", "2024-02-30") is None  # 존재하지 않는 날짜
    assert D._parse_full_date(None, src) is None


class _FlakyLLM(_FakeLLM):
    # 경험 중 problem이 fail_problem인 호출만 실패시킨다
    def __init__(self, content, fail_problem):
        super().__init__(content)
        self.fail_problem = fail_problem

    def invoke(self, messages):
        if f"problem: {self.fail_problem}" in messages[1].content:
            raise RuntimeError("llm down")
        return super().invoke(messages)


def _element(problem: str) -> ExtractedElements:
    return ExtractedElements(
        problem=problem,
        cause=None,
        approach="해결",
        rationale=None,
        outcome=None,
        source_chunk_ids=["d:0"],
    )


def test_failed_experience_is_skipped_and_the_rest_continue(monkeypatch):
    monkeypatch.setattr(
        D, "get_llm", lambda: _FlakyLLM(_content([("d:0", "첫째 문장.")]), "나쁜 문제")
    )
    state = {
        "chunks": CHUNKS,
        "extracted_elements": [
            _element("좋은 문제"),
            _element("나쁜 문제"),
            _element("또 좋은 문제"),
        ],
        "warnings": ["앞선 경고"],
    }
    result = D.draft_card(state)
    assert len(result["card_drafts"]) == 2 and "error" not in result
    assert result["warnings"][0] == "앞선 경고" and "나쁜 문제" in result["warnings"][1]


def test_all_experiences_failing_fails_the_job(monkeypatch):
    monkeypatch.setattr(D, "get_llm", lambda: _FlakyLLM(_content([]), "나쁜 문제"))
    result = D.draft_card({"chunks": CHUNKS, "extracted_elements": [_element("나쁜 문제")]})
    assert "카드 초안 생성 실패" in result["error"] and "모두 실패" in result["error"]


def test_no_experiences_is_a_normal_empty_result(monkeypatch):
    monkeypatch.setattr(D, "get_llm", lambda: _FakeLLM(_content([])))
    assert D.draft_card({"chunks": CHUNKS, "extracted_elements": []}) == {"card_drafts": []}


def test_technologies_are_normalized_and_deduped(monkeypatch):
    content = _content([("d:0", "첫째 문장.")])
    content.technologies = ["Next.JS", "넥스트", "Fast API", "Brand New"]
    monkeypatch.setattr(D, "get_llm", lambda: _FakeLLM(content))
    result = D.draft_card({"chunks": CHUNKS, "extracted_elements": [ELEMENT]})
    assert result["card_drafts"][0].technologies == ["nextjs", "fastapi", "brandnew"]
