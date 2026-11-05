def test_unavailable_agent_returns_explicit_error(client):
    response = client.post("/api/agent/ask", json={"question": "어떻게 해결했나요?"})
    assert response.status_code == 503
    assert response.json() == {"detail": "지식카드 검색 기능이 아직 준비되지 않았습니다."}
