from uuid import uuid4

import pytest

from app.domains.documents.pipeline.models import Evidence, Experience, Experiences
from app.domains.documents.pipeline.nodes import extract as EX

DOC1, DOC2 = uuid4(), uuid4()


@pytest.fixture(autouse=True)
def fake_project_meta(monkeypatch):
    # 소개 정보는 test_project_meta.py에서 따로 본다
    monkeypatch.setattr(EX, "describe_project", lambda _message, _chunks: {"team": "결제팀"})


def _state() -> dict:
    texts = {
        (DOC1, 0): "주문 두 건이 동시에 할당됐다. 처음에는 메시지 중복을 의심했다.",
        (DOC1, 1): "원인은 읽기와 쓰기가 분리된 경쟁 조건이다. 재고 행 잠금을 채택했다.",
        (DOC2, 0): "| 시험 | 결과 |\n|---|---|\n| 동시 할당 100회 | 초과 할당 0건 |",
    }
    return {
        "documents": [
            {
                "id": DOC1,
                "storage_bucket": "b",
                "storage_path": "p1",
                "original_filename": "메모.txt",
            },
            {
                "id": DOC2,
                "storage_bucket": "b",
                "storage_path": "p2",
                "original_filename": "시험.xlsx",
            },
        ],
        "chunks": [
            {"id": f"{d}:{i}", "document_id": d, "index": i, "text": t, "location": ""}
            for (d, i), t in texts.items()
        ],
    }


class _LLM:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.messages = result, error, None

    def with_structured_output(self, _schema):
        return self

    def invoke(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        return self.result


def _ev(kind, chunk_id, quote) -> Evidence:
    return Evidence(kind=kind, statement="s", chunk_id=chunk_id, quote=quote)


def _run(monkeypatch, *experiences, error=None, project_name=None) -> tuple[dict, _LLM]:
    llm = _LLM(Experiences(experiences=list(experiences), project_name=project_name), error)
    monkeypatch.setattr(EX, "get_llm", lambda: llm)
    return EX.extract(_state()), llm


def _exp(*evidences) -> Experience:
    return Experience(title="t", summary="s", link_hints=[], evidences=list(evidences))


def test_prompt_uses_short_aliases_and_results_map_back_to_real_chunk_ids(monkeypatch):
    result, llm = _run(
        monkeypatch,
        _exp(
            _ev("cause", "D1:1", "원인은 읽기와 쓰기가 분리된 경쟁 조건이다."),
            _ev("result", "D2:0", "| 동시 할당 100회 | 초과 할당 0건 |"),
        ),
    )
    message = llm.messages[1].content
    assert (
        '<chunk id="D1:0"' in message
        and "# 문서 D2: 시험.xlsx" in message
        and str(DOC1) not in message
    )
    [experience] = result["experiences"]
    assert [e.chunk_id for e in experience.evidences] == [f"{DOC1}:1", f"{DOC2}:0"]


def test_wrong_chunk_id_is_corrected_to_the_chunk_that_has_the_quote(monkeypatch):
    result, _ = _run(monkeypatch, _exp(_ev("decision", "D1:0", "재고 행 잠금을 채택했다.")))
    assert result["experiences"][0].evidences[0].chunk_id == f"{DOC1}:1"


def test_quote_not_in_the_document_is_dropped_but_the_experience_stays(monkeypatch):
    result, _ = _run(
        monkeypatch,
        _exp(
            _ev("decision", "D1:1", "재고 행 잠금을 채택했다."),
            _ev("result", "D1:1", "응답 시간이 40% 줄었다."),  # 원문에 없음
            _ev("result", "D2:0", "원인은 읽기와 쓰기가 분리된 경쟁 조건이다."),  # 다른 문서의 문장
        ),
    )
    assert [e.kind for e in result["experiences"][0].evidences] == ["decision"]


def test_elided_quote_passes_only_if_every_piece_is_in_the_source(monkeypatch):
    ok = _ev("observation", "D1:0", "주문 두 건이 동시에 할당됐다. ... 메시지 중복을 의심했다.")
    stitched = _ev("cause", "D1:0", "주문 두 건이 동시에 할당됐다. ... 서버 두 대가 돌았다.")
    result, _ = _run(
        monkeypatch, _exp(ok, stitched, _ev("decision", "D1:1", "재고 행 잠금을 채택했다."))
    )
    assert [e.kind for e in result["experiences"][0].evidences] == ["observation", "decision"]


def test_experience_without_cause_decision_action_or_result_is_dropped(monkeypatch):
    plan_only = _exp(
        _ev("observation", "D1:0", "주문 두 건이 동시에 할당됐다."),
        _ev("plan", "D1:0", "처음에는 메시지 중복을 의심했다."),
    )
    ungrounded_fact = _exp(_ev("action", "D1:0", "캐시를 도입했다."))  # 남는 근거가 없음
    result, _ = _run(monkeypatch, plan_only, ungrounded_fact)
    assert result["experiences"] == []


def test_llm_failure_becomes_an_error(monkeypatch):
    result, _ = _run(monkeypatch, error=RuntimeError("terra down"))
    assert result["error"].startswith("경험 추출 실패")


def test_no_chunks_skips_the_llm(monkeypatch):
    monkeypatch.setattr(
        EX, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("호출하면 안 됨"))
    )
    assert EX.extract({**_state(), "chunks": []}) == {"experiences": []}


def test_investigation_with_only_a_rejected_hypothesis_is_kept(monkeypatch):
    # 원인을 못 찾은 조사라도 가설을 기각한 근거가 있으면 진단 교훈의 후보로 남긴다
    result, _ = _run(
        monkeypatch,
        _exp(
            _ev("observation", "D1:0", "주문 두 건이 동시에 할당됐다."),
            _ev("rejected_hypothesis", "D1:0", "처음에는 메시지 중복을 의심했다."),
        ),
    )
    assert len(result["experiences"]) == 1


def test_project_name_found_in_source_is_kept_as_written(monkeypatch):
    result, _ = _run(monkeypatch, project_name=" 경쟁 조건 ")
    assert result["project_name"] == "경쟁 조건"


def test_project_name_missing_from_source_is_dropped(monkeypatch):
    result, _ = _run(monkeypatch, project_name="없는 프로젝트")
    assert result["project_name"] is None


def test_project_meta_is_returned_with_experiences(monkeypatch):
    result, _ = _run(monkeypatch)
    assert result["project_meta"] == {"team": "결제팀"}


def test_project_name_can_be_absent(monkeypatch):
    result, _ = _run(monkeypatch, project_name=None)
    assert result["project_name"] is None
