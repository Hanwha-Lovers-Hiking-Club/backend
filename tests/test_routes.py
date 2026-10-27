def test_domain_routes_are_under_api_prefix(client):
    paths = list(client.get("/openapi.json").json()["paths"])

    assert "/api/agent/ask" in paths
    assert "/api/chat/stream" in paths
    assert [path for path in paths if not path.startswith("/api/")] == ["/health"]
