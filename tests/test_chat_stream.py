# POST /api/chat/stream (SSE) 테스트. 의도 파악/검색/검증/생성은 가짜로 바꿔서 service의 순서와
# 분기만 확인한다 (LLM, 임베딩, DB를 호출하지 않는다).
import json

import pytest

from app.domains.chat import service
from app.domains.chat.generator import NO_CARD_MESSAGE, OFF_TOPIC_MESSAGE
from app.domains.chat.models import Candidate, Intent, VerifiedCandidate

URL = "/api/chat/stream"


def _intent(**overrides) -> Intent:
    data = {
        "kind": "solution",
        "search_query": "RAG 검색 정확도를 높인 방법",
        "needs_clarification": False,
        "clarifying_question": None,
        **overrides,
    }
    return Intent(**data)


def _events(text: str) -> list[tuple[str, dict]]:
    # SSE 본문을 (이벤트 이름, data JSON) 목록으로 바꾼다
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def _answer(events: list[tuple[str, dict]]) -> str:
    return "".join(data["content"] for name, data in events if name == "token")


@pytest.fixture
def pipeline(monkeypatch, make_card):
    # 정상 흐름용 가짜 단계들. 각 테스트가 필요하면 monkeypatch로 다시 덮어쓴다
    calls = {"generate": [], "search": 0, "verify": 0}
    card = make_card()

    async def fake_intent(message, history):
        calls["intent"] = (message, history)
        return _intent()

    async def fake_search(intent):
        calls["search"] += 1
        return [Candidate(card=card, similarity=0.8)]

    async def fake_verify(question, candidates):
        calls["verify"] += 1
        calls["verify_question"] = question
        return [
            VerifiedCandidate(candidate=c, reason="질문과 같은 문제를 다룹니다.")
            for c in candidates
        ]

    async def fake_generate(message, history, kind, cards):
        calls["generate"].append((kind, cards))
        for token in ["하이브리드 ", "검색을 썼어요."]:
            yield token

    monkeypatch.setattr(service, "understand_intent", fake_intent)
    monkeypatch.setattr(service, "search_cards", fake_search)
    monkeypatch.setattr(service, "verify_candidates", fake_verify)
    monkeypatch.setattr(service, "generate_answer", fake_generate)
    calls["card"] = card
    return calls


def test_streams_tokens_then_references_then_completed(client, pipeline):
    response = client.post(URL, json={"message": "RAG 검색 정확도를 개선한 사례가 있어?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "token", "references", "completed"]
    assert _answer(events) == "하이브리드 검색을 썼어요."
    assert events[-1][1] == {}


def test_references_match_frontend_chat_reference(client, pipeline):
    # 프론트 src/features/chat/types.ts의 ChatReference 모양
    response = client.post(URL, json={"message": "질문"})

    [reference] = dict(_events(response.text))["references"]["items"]
    assert set(reference) == {"id", "type", "label", "card", "reason", "document"}
    assert reference["type"] == "knowledge-card"
    assert reference["reason"] == "질문과 같은 문제를 다룹니다."

    card = reference["card"]
    assert reference["label"] == card["title"] == pipeline["card"].title
    assert {"projectName", "qualityScore", "createdAt", "evidences"} <= set(card)

    [evidence] = card["evidences"]
    assert reference["id"] == evidence["id"]
    assert reference["document"] == {
        "id": evidence["documentId"],
        "documentTitle": evidence["documentTitle"],
        "location": evidence["location"],
        "excerpt": evidence["excerpt"],
    }


def test_answer_is_generated_from_verified_cards_with_intent_kind(client, pipeline):
    client.post(URL, json={"message": "질문"})

    [(kind, cards)] = pipeline["generate"]
    assert kind == "solution"
    assert [c.title for c in cards] == [pipeline["card"].title]
    # 검증 에이전트는 이전 대화가 풀어 쓰인 검색 문장을 받는다
    assert pipeline["verify_question"] == "RAG 검색 정확도를 높인 방법"


def test_history_is_passed_to_intent(client, pipeline):
    client.post(
        URL,
        json={
            "message": "질문",
            "history": [
                {"role": "user", "content": "RAG 검색에 대해 설명해줘."},
                {"role": "assistant", "content": "RAG는 외부 지식을 검색하는 방식입니다."},
            ],
        },
    )

    message, history = pipeline["intent"]
    assert message == "질문"
    assert [(m.role, m.content) for m in history] == [
        ("user", "RAG 검색에 대해 설명해줘."),
        ("assistant", "RAG는 외부 지식을 검색하는 방식입니다."),
    ]


def test_verification_can_be_disabled(client, pipeline, monkeypatch):
    monkeypatch.setattr(service.settings, "CHAT_VERIFY_ENABLED", False)

    response = client.post(URL, json={"message": "질문"})

    assert pipeline["verify"] == 0
    [reference] = dict(_events(response.text))["references"]["items"]
    assert "유사도 0.80" in reference["reason"]


def test_references_are_capped(client, pipeline, monkeypatch, make_card):
    cards = [make_card(f"카드 {i}") for i in range(5)]

    async def many(question, candidates):
        return [
            VerifiedCandidate(candidate=Candidate(card=c, similarity=0.9), reason="관련 있음")
            for c in cards
        ]

    monkeypatch.setattr(service, "verify_candidates", many)

    response = client.post(URL, json={"message": "질문"})

    items = dict(_events(response.text))["references"]["items"]
    assert [item["label"] for item in items] == ["카드 0", "카드 1", "카드 2"]
    assert len(pipeline["generate"][0][1]) == service.MAX_REFERENCES


def test_no_verified_cards_generates_with_empty_cards(client, pipeline, monkeypatch):
    async def none_passed(question, candidates):
        return []

    monkeypatch.setattr(service, "verify_candidates", none_passed)

    response = client.post(URL, json={"message": "질문"})

    assert pipeline["generate"] == [("solution", [])]
    assert dict(_events(response.text))["references"]["items"] == []


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
    assert dict(events)["references"]["items"] == []
    assert pipeline["search"] == 0
    assert pipeline["generate"] == []


def test_clarification_flag_without_question_still_searches(client, pipeline, monkeypatch):
    async def broken_flag(message, history):
        return _intent(needs_clarification=True, clarifying_question=None)

    monkeypatch.setattr(service, "understand_intent", broken_flag)

    client.post(URL, json={"message": "질문"})

    assert pipeline["search"] == 1


def test_off_topic_answers_with_notice_without_searching(client, pipeline, monkeypatch):
    async def chitchat(message, history):
        return _intent(kind="off_topic")

    monkeypatch.setattr(service, "understand_intent", chitchat)

    response = client.post(URL, json={"message": "오늘 점심 뭐 먹지"})

    events = _events(response.text)
    assert [name for name, _ in events] == ["token", "references", "completed"]
    assert _answer(events) == OFF_TOPIC_MESSAGE
    assert pipeline["search"] == 0


def test_no_cards_found_answers_with_notice(client, monkeypatch):
    # generate_answer는 진짜를 쓴다: 카드가 없으면 LLM을 부르지 않고 고정 문구로 답한다
    async def fake_intent(message, history):
        return _intent()

    async def nothing(intent):
        return []

    monkeypatch.setattr(service, "understand_intent", fake_intent)
    monkeypatch.setattr(service, "search_cards", nothing)

    response = client.post(URL, json={"message": "질문"})

    events = _events(response.text)
    assert _answer(events) == NO_CARD_MESSAGE
    assert dict(events)["references"]["items"] == []
    assert events[-1][0] == "completed"


@pytest.mark.parametrize("failing", ["understand_intent", "search_cards", "generate_answer"])
def test_failure_sends_error_event(client, pipeline, monkeypatch, failing):
    def broken(*args):
        raise RuntimeError("실패")

    monkeypatch.setattr(service, failing, broken)

    response = client.post(URL, json={"message": "질문"})

    assert response.status_code == 200
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
