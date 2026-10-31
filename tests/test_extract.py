from app.domains.documents.pipeline.models import ExtractedElements, ExtractedElementsBatch
from app.domains.documents.pipeline.nodes import extract as E

CHUNKS = [
    {"id": "d:0", "document_id": "d", "text": "본문0", "location": ""},
    {"id": "d:1", "document_id": "d", "text": "본문1", "location": ""},
]


def _element(ids: list[str]) -> ExtractedElements:
    return ExtractedElements(
        problem="문제",
        cause=None,
        approach="해결",
        rationale=None,
        outcome=None,
        source_chunk_ids=ids,
    )


class _FakeLLM:
    def __init__(self, elements):
        self.elements, self.messages = elements, None

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages = messages
        return ExtractedElementsBatch(elements=self.elements)


def _run(monkeypatch, elements, chunks=CHUNKS):
    fake = _FakeLLM(elements)
    monkeypatch.setattr(E, "get_llm", lambda: fake)
    return E.extract({"chunks": chunks}), fake


def test_invalid_and_duplicate_ids_are_removed_and_empty_element_dropped(monkeypatch):
    result, _ = _run(
        monkeypatch,
        [_element(["d:0", "d:0", "x:9"]), _element(["nope"]), _element(["d:1"])],
    )
    ids = [e.source_chunk_ids for e in result["extracted_elements"]]
    assert ids == [["d:0"], ["d:1"]]


def test_prompt_and_chunks_are_separate_messages(monkeypatch):
    _, fake = _run(monkeypatch, [])
    system, human = fake.messages
    assert system.type == "system" and human.type == "human"
    assert (
        '<chunk id="d:0"' in human.content and "<chunk" not in system.content.split("\n", 1)[1][:0]
    )


def test_no_chunks_skips_llm(monkeypatch):
    monkeypatch.setattr(E, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("LLM 호출됨")))
    assert E.extract({"chunks": []}) == {"extracted_elements": []}
