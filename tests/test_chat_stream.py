# POST /api/chat/stream (SSE) 테스트. 토큰 사이 대기(_wait)는 가짜로 바꿔 바로 진행한다.
import json

import pytest

from app.domains.chat import service

URL = "/api/chat/stream"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    async def fake_wait():
        pass

    monkeypatch.setattr(service, "_wait", fake_wait)


def _events(text: str) -> list[tuple[str, dict]]:
    # SSE 본문을 (이벤트 이름, data JSON) 목록으로 바꾼다
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_streams_tokens_then_references_then_completed(client):
    response = client.post(URL, json={"message": "RAG 검색 정확도를 개선한 사례가 있어?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names[-2:] == ["references", "completed"]
    assert set(names[:-2]) == {"token"}
    assert len(names) > 3
    assert events[-1][1] == {}


def test_answer_is_marked_as_mock(client):
    response = client.post(URL, json={"message": "질문"})

    events = _events(response.text)
    answer = "".join(data["content"] for name, data in events if name == "token")
    assert answer.startswith("[Mock]")
    assert "질문" in answer


def test_references_use_camel_case(client):
    response = client.post(URL, json={"message": "질문"})

    references = dict(_events(response.text))["references"]
    assert references == {
        "items": [
            {
                "cardId": "00000000-0000-0000-0000-000000000000",
                "reason": "[Mock] 실제 검색 결과가 아닌 테스트 근거입니다.",
                "documentId": "00000000-0000-0000-0000-000000000000",
                "location": "Mock 위치",
                "excerpt": "Mock 근거 발췌문입니다.",
            }
        ]
    }


def test_history_is_passed_to_service(client, monkeypatch):
    received = {}
    original = service._retrieve_references

    async def spy(message, history):
        received["history"] = history
        return await original(message, history)

    monkeypatch.setattr(service, "_retrieve_references", spy)

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

    assert [(m.role, m.content) for m in received["history"]] == [
        ("user", "RAG 검색에 대해 설명해줘."),
        ("assistant", "RAG는 외부 지식을 검색하는 방식입니다."),
    ]


def test_failure_sends_error_event(client, monkeypatch):
    async def broken(message, history):
        raise RuntimeError("검색 실패")

    monkeypatch.setattr(service, "_retrieve_references", broken)

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
