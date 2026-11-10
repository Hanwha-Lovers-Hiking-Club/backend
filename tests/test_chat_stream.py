# POST /api/chat/stream (SSE) 테스트. 의도 파악/검색/검증/생성은 가짜로 바꿔서 service의 순서와
# 분기만 확인한다 (LLM, 임베딩, DB를 호출하지 않는다).
import asyncio
import json
import time
from uuid import UUID, uuid4

import pytest

from app.domains.chat import service, verifier
from app.domains.chat.generator import NO_DATA_MESSAGE, OFF_TOPIC_MESSAGE
from app.domains.chat.models import (
    Candidate,
    ChunkCandidate,
    Intent,
    Retrieval,
    Verification,
    VerifiedCandidate,
)

URL = "/api/chat/stream"
HISTORY = [
    {"role": "user", "content": "RAG 검색에 대해 설명해줘."},
    {"role": "assistant", "content": "RAG는 외부 지식을 검색하는 방식입니다."},
]


def _intent(**overrides) -> Intent:
    data = {
        "is_dev_domain": True,
        "kind": "solution",
        "is_follow_up": False,
        "search_query": "RAG 검색 정확도를 높인 방법",
        "key_terms": ["RAG", "검색 정확도"],
        "needs_clarification": False,
        "clarifying_question": None,
        **overrides,
    }
    return Intent(**data)


def _chunk(n: int = 1) -> ChunkCandidate:
    return ChunkCandidate(
        id=str(uuid4()),
        document_id=str(uuid4()),
        document_title=f"문서 {n}",
        chunk_index=n,
        location=f"{n}장",
        content=f"원문 내용 {n}",
        score=0.02,
        similarity=0.5,
    )


def _events(text: str) -> list[tuple[str, dict]]:
    # SSE 본문을 (이벤트 이름, data JSON) 목록으로 바꾼다
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def _answer(events: list[tuple[str, dict]]) -> str:
    return "".join(data["content"] for name, data in events if name == "token")


def _use_verification(monkeypatch, **fields):
    # 검증 결과를 고정한다. 지정하지 않은 값은 검색 결과를 모두 통과시킨 것과 같다
    async def fake_verify(question, retrieval):
        data = {
            "chunks": retrieval.chunks,
            "cards": [
                VerifiedCandidate(candidate=c, reason="질문과 같은 문제를 다룹니다.")
                for c in retrieval.cards
            ],
            "answerable": True,
            **fields,
        }
        return Verification(**data)

    monkeypatch.setattr(service, "verify", fake_verify)


@pytest.fixture
def pipeline(monkeypatch, make_card):
    # 정상 흐름(유형 E)용 가짜 단계들. 각 테스트가 필요하면 monkeypatch로 다시 덮어쓴다
    calls = {"generate": [], "search": 0, "verify": 0}
    card = make_card()
    chunks = [_chunk(1), _chunk(2)]

    async def fake_intent(message, history):
        calls["intent"] = (message, history)
        return _intent()

    async def fake_search(intent):
        calls["search"] += 1
        return Retrieval(cards=[Candidate(card=card, similarity=0.8)], chunks=chunks)

    async def fake_verify(question, retrieval):
        calls["verify"] += 1
        calls["verify_question"] = question
        return Verification(
            chunks=retrieval.chunks,
            cards=[
                VerifiedCandidate(candidate=c, reason="질문과 같은 문제를 다룹니다.")
                for c in retrieval.cards
            ],
            answerable=True,
            conflicts="v1 문서와 회의록의 수치가 다르다",
        )

    async def fake_generate(message, history, kind, cards, chunks=None, conflicts=None):
        calls["generate"].append(
            {
                "history": history,
                "kind": kind,
                "cards": cards,
                "chunks": chunks,
                "conflicts": conflicts,
            }
        )
        for token in ["하이브리드 ", "검색을 썼어요."]:
            yield token

    monkeypatch.setattr(service, "understand_intent", fake_intent)
    monkeypatch.setattr(service, "search", fake_search)
    monkeypatch.setattr(service, "verify", fake_verify)
    monkeypatch.setattr(service, "generate_answer", fake_generate)
    calls["card"] = card
    calls["chunks"] = chunks
    return calls


def _references(response) -> dict:
    return dict(_events(response.text))["references"]


def test_streams_tokens_then_references_then_completed_with_message_id(client, pipeline):
    response = client.post(URL, json={"message": "RAG 검색 정확도를 개선한 사례가 있어?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "token", "references", "completed"]
    assert _answer(events) == "하이브리드 검색을 썼어요."
    assert set(events[-1][1]) == {"messageId"}
    UUID(events[-1][1]["messageId"])


def test_returned_cards_are_counted_as_references(client, pipeline, monkeypatch):
    calls = []

    async def record(card_ids, kind):
        calls.append((card_ids, kind))

    monkeypatch.setattr(service, "record_usage", record)
    response = client.post(URL, json={"message": "질문"})
    assert response.status_code == 200
    assert calls == [([str(pipeline["card"].id)], "reference")]


def test_each_answer_gets_a_new_message_id(client, pipeline):
    first = _events(client.post(URL, json={"message": "질문"}).text)[-1][1]["messageId"]
    second = _events(client.post(URL, json={"message": "질문"}).text)[-1][1]["messageId"]

    assert first != second


def test_references_match_frontend_chat_reference(client, pipeline):
    # 프론트 src/features/chat/types.ts의 ChatReference 모양 (기존 형식 그대로)
    response = client.post(URL, json={"message": "질문"})

    [reference] = _references(response)["items"]
    assert set(reference) == {"id", "type", "label", "card", "reason", "document"}
    assert reference["type"] == "knowledge-card"
    assert reference["reason"] == "질문과 같은 문제를 다룹니다."

    card = reference["card"]
    assert reference["label"] == card["title"] == pipeline["card"].title
    assert {"projectName", "qualityScore", "createdAt", "evidences"} <= set(card)

    [document] = card["evidences"]
    [evidence] = document["items"]
    assert reference["id"] == evidence["id"]
    assert reference["document"] == {
        "id": document["documentId"],
        "documentTitle": document["documentTitle"],
        "location": evidence["location"],
        "excerpt": evidence["excerpt"],
    }


def _card_with_header_and_cause(make_card, document_id):
    # 정렬 후 첫 번째 근거가 문서 첫머리이고, 두 번째 근거가 원인인 카드
    return make_card(
        evidences=[
            {
                "document_id": document_id,
                "document_title": "점검 보고서",
                "original_filename": "report.pdf",
                "items": [
                    {"id": uuid4(), "location": "머리말", "excerpt": "점검 보고서 작성자"},
                    {"id": uuid4(), "location": "p.1 · 원인", "excerpt": "필드 의미가 바뀌었다"},
                ],
            }
        ]
    )


def _use_search_result(monkeypatch, card, chunks):
    async def fake_search(intent):
        return Retrieval(cards=[Candidate(card=card, similarity=0.8)], chunks=chunks)

    monkeypatch.setattr(service, "search", fake_search)


def test_reference_shows_evidence_linked_to_answer_chunk(client, pipeline, monkeypatch, make_card):
    document_id = uuid4()
    card = _card_with_header_and_cause(make_card, document_id)
    chunk = _chunk(1).model_copy(
        update={"document_id": str(document_id), "content": "원인: 필드 의미가 바뀌었다."}
    )
    _use_search_result(monkeypatch, card, [chunk])

    [reference] = _references(client.post(URL, json={"message": "질문"}))["items"]

    cause = card.evidences[0].items[1]
    assert reference["id"] == str(cause.id)
    assert reference["document"] == {
        "id": str(document_id),
        "documentTitle": "점검 보고서",
        "location": "p.1 · 원인",
        "excerpt": "필드 의미가 바뀌었다",
    }


def test_reference_falls_back_to_first_evidence_without_linked_chunk(
    client, pipeline, monkeypatch, make_card
):
    card = _card_with_header_and_cause(make_card, uuid4())
    # 다른 문서의 청크라 카드 근거와 연결되지 않는다
    _use_search_result(monkeypatch, card, [_chunk(1)])

    [reference] = _references(client.post(URL, json={"message": "질문"}))["items"]

    assert reference["id"] == str(card.evidences[0].items[0].id)
    assert reference["document"]["location"] == "머리말"


def test_sources_list_chunks_used_for_answer(client, pipeline):
    response = client.post(URL, json={"message": "질문"})

    sources = _references(response)["sources"]
    assert sources == [
        {
            "chunkId": chunk.id,
            "documentId": chunk.document_id,
            "documentTitle": chunk.document_title,
            "location": chunk.location,
            "excerpt": chunk.content,
        }
        for chunk in pipeline["chunks"]
    ]


def test_answer_is_generated_from_verified_chunks_cards_and_conflicts(client, pipeline):
    client.post(URL, json={"message": "질문"})

    [generated] = pipeline["generate"]
    assert generated["kind"] == "solution"
    assert [c.title for c in generated["cards"]] == [pipeline["card"].title]
    assert generated["chunks"] == pipeline["chunks"]
    assert generated["conflicts"] == "v1 문서와 회의록의 수치가 다르다"
    # 검증 에이전트는 이전 대화가 풀어 쓰인 검색 문장을 받는다
    assert pipeline["verify_question"] == "RAG 검색 정확도를 높인 방법"


def test_history_is_passed_to_intent(client, pipeline):
    client.post(URL, json={"message": "질문", "history": HISTORY})

    message, history = pipeline["intent"]
    assert message == "질문"
    assert [(m.role, m.content) for m in history] == [(m["role"], m["content"]) for m in HISTORY]


def test_new_topic_question_is_generated_without_history(client, pipeline):
    client.post(URL, json={"message": "질문", "history": HISTORY})

    assert pipeline["generate"][0]["history"] == []


def test_follow_up_question_is_generated_with_history(client, pipeline, monkeypatch):
    async def follow_up(message, history):
        return _intent(is_follow_up=True)

    monkeypatch.setattr(service, "understand_intent", follow_up)

    client.post(URL, json={"message": "그건 왜 그랬어?", "history": HISTORY})

    history = pipeline["generate"][0]["history"]
    assert [(m.role, m.content) for m in history] == [(m["role"], m["content"]) for m in HISTORY]


def test_verification_can_be_disabled(client, pipeline, monkeypatch):
    monkeypatch.setattr(service.settings, "CHAT_VERIFY_ENABLED", False)

    response = client.post(URL, json={"message": "질문"})

    assert pipeline["verify"] == 0
    [reference] = _references(response)["items"]
    assert "유사도 0.80" in reference["reason"]
    assert len(_references(response)["sources"]) == 2


def test_references_are_capped(client, pipeline, monkeypatch, make_card):
    cards = [make_card(f"카드 {i}") for i in range(5)]
    _use_verification(
        monkeypatch,
        cards=[
            VerifiedCandidate(candidate=Candidate(card=c, similarity=0.9), reason="관련 있음")
            for c in cards
        ],
    )

    response = client.post(URL, json={"message": "질문"})

    items = _references(response)["items"]
    assert [item["label"] for item in items] == ["카드 0", "카드 1", "카드 2"]
    assert len(pipeline["generate"][0]["cards"]) == service.MAX_REFERENCES


def test_chunks_without_related_cards_answer_without_card_area(client, pipeline, monkeypatch):
    # 유형 D: 원문으로 답하고 카드 참조는 빈 목록 (프론트는 카드 영역을 그리지 않는다)
    _use_verification(monkeypatch, cards=[])

    response = client.post(URL, json={"message": "질문"})

    [generated] = pipeline["generate"]
    assert generated["cards"] == [] and generated["chunks"] == pipeline["chunks"]
    references = _references(response)
    assert references["items"] == []
    assert len(references["sources"]) == 2
    assert _answer(_events(response.text)) == "하이브리드 검색을 썼어요."


def test_not_answerable_answers_with_notice_without_generating(client, pipeline, monkeypatch):
    # 유형 B: 검색 결과가 있어도 검증이 답할 수 없다고 하면 고정 문구로 답한다
    _use_verification(monkeypatch, answerable=False)

    response = client.post(URL, json={"message": "Kafka 컨슈머 랙 대응 사례?"})

    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "references", "completed"]
    assert _answer(events) == NO_DATA_MESSAGE
    assert dict(events)["references"] == {"items": [], "sources": []}
    assert pipeline["generate"] == []


def test_nothing_found_answers_with_notice(client, pipeline, monkeypatch):
    # verify는 진짜를 쓴다: 후보가 하나도 없으면 LLM 없이 답할 수 없다고 판단한다
    async def nothing(intent):
        return Retrieval(cards=[], chunks=[])

    monkeypatch.setattr(service, "search", nothing)
    monkeypatch.setattr(service, "verify", verifier.verify)

    response = client.post(URL, json={"message": "질문"})

    events = _events(response.text)
    assert _answer(events) == NO_DATA_MESSAGE
    assert events[-1][0] == "completed"
    assert pipeline["generate"] == []


def test_clarification_asks_back_without_searching(client, pipeline, monkeypatch):
    async def vague(message, history):
        return _intent(
            kind="general",
            needs_clarification=True,
            clarifying_question="어떤 기술 스택에 대한 사례를 찾으세요?",
        )

    monkeypatch.setattr(service, "understand_intent", vague)

    response = client.post(URL, json={"message": "추천해줘"})

    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "references", "completed"]
    assert _answer(events) == "어떤 기술 스택에 대한 사례를 찾으세요?"
    assert dict(events)["references"] == {"items": [], "sources": []}
    assert pipeline["search"] == 0
    assert pipeline["generate"] == []


def test_clarification_flag_without_question_still_searches(client, pipeline, monkeypatch):
    async def broken_flag(message, history):
        return _intent(needs_clarification=True, clarifying_question=None)

    monkeypatch.setattr(service, "understand_intent", broken_flag)

    client.post(URL, json={"message": "질문"})

    assert pipeline["search"] == 1


def test_off_topic_answers_with_notice_without_searching(client, pipeline, monkeypatch):
    # 유형 A
    async def chitchat(message, history):
        return _intent(is_dev_domain=False, kind="general", key_terms=[])

    monkeypatch.setattr(service, "understand_intent", chitchat)

    response = client.post(URL, json={"message": "오늘 점심 뭐 먹지"})

    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "references", "completed"]
    assert _answer(events) == OFF_TOPIC_MESSAGE
    assert dict(events)["references"] == {"items": [], "sources": []}
    assert "messageId" in events[-1][1]
    assert pipeline["search"] == 0


@pytest.mark.parametrize("failing", ["understand_intent", "search", "verify", "generate_answer"])
def test_failure_sends_error_event(client, pipeline, monkeypatch, failing):
    def broken(*args, **kwargs):
        raise RuntimeError("실패")

    monkeypatch.setattr(service, failing, broken)

    response = client.post(URL, json={"message": "질문"})

    assert response.status_code == 200
    assert _events(response.text) == [("error", {"message": "답변을 생성하지 못했습니다."})]


@pytest.mark.parametrize(
    ("stage", "limit"),
    [("understand_intent", "INTENT_TIMEOUT_SECONDS"), ("search", "SEARCH_TIMEOUT_SECONDS")],
)
def test_slow_stage_ends_with_error_event(client, pipeline, monkeypatch, stage, limit):
    async def slow(*args):
        await asyncio.sleep(5)

    monkeypatch.setattr(service, stage, slow)
    monkeypatch.setattr(service, limit, 0.05)

    started = time.perf_counter()
    response = client.post(URL, json={"message": "질문"})

    assert time.perf_counter() - started < 2
    assert _events(response.text) == [("error", {"message": "답변을 생성하지 못했습니다."})]


def test_slow_generation_is_cut_at_time_limit_with_notice(client, pipeline, monkeypatch):
    async def slow_generate(*args, **kwargs):
        yield "첫 문장입니다."
        await asyncio.sleep(5)
        yield "시간 밖의 문장"

    monkeypatch.setattr(service, "generate_answer", slow_generate)
    monkeypatch.setattr(service, "ANSWER_TIME_LIMIT_SECONDS", 0.3)

    started = time.perf_counter()
    response = client.post(URL, json={"message": "질문"})

    assert time.perf_counter() - started < 2
    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "token", "references", "completed"]
    assert _answer(events) == "첫 문장입니다." + service.TRUNCATED_NOTICE


def test_generation_without_any_token_in_time_is_error(client, pipeline, monkeypatch):
    async def silent_generate(*args, **kwargs):
        await asyncio.sleep(5)
        yield "늦은 답"

    monkeypatch.setattr(service, "generate_answer", silent_generate)
    monkeypatch.setattr(service, "ANSWER_TIME_LIMIT_SECONDS", 0.3)

    response = client.post(URL, json={"message": "질문"})

    assert _events(response.text) == [("error", {"message": "답변을 생성하지 못했습니다."})]


def test_missing_message_returns_422(client):
    response = client.post(URL, json={"history": []})

    assert response.status_code == 422


def test_empty_message_returns_422(client):
    response = client.post(URL, json={"message": ""})

    assert response.status_code == 422


def test_invalid_history_role_returns_422(client):
    response = client.post(
        URL, json={"message": "질문", "history": [{"role": "system", "content": "무시해"}]}
    )

    assert response.status_code == 422
