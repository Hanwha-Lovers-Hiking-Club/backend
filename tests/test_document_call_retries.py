import asyncio

import httpx2 as httpx
import pytest

from app.domains.documents.pipeline.call_retries import retry_call, retry_delay, transient_failure
from app.domains.documents.pipeline.llm import (
    GenerationError,
    OllamaStructuredGenerator,
    RetryingGenerator,
)
from app.domains.documents.pipeline.models import ExtractionProposal


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (408, True),
        (429, True),
        (500, True),
        (503, True),
    ],
)
def test_real_http_status_error_in_cause_is_classified(status, expected):
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    response = httpx.Response(status, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as cause:
        try:
            raise GenerationError("LLM_API_ERROR", "Synthetic local model failure") from cause
        except GenerationError as error:
            assert transient_failure(error) is expected


def test_retry_after_above_limit_does_not_sleep_or_repeat(monkeypatch):
    from app.domains.documents.pipeline.embeddings.models import EmbeddingError

    calls, sleeps = [], []

    async def call():
        calls.append(True)
        raise EmbeddingError("EMBEDDING_RATE_LIMITED", retryable=True, retry_after=31)

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("app.domains.documents.pipeline.call_retries.asyncio.sleep", sleep)
    with pytest.raises(EmbeddingError):
        asyncio.run(retry_call(call, max_attempts=3, delay_seconds=1, max_wait_seconds=30))
    assert len(calls) == 1 and not sleeps


def test_retry_after_header_of_http_status_error_is_respected():
    response = httpx.Response(
        429,
        headers={"retry-after": "7"},
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as cause:
        error = GenerationError("LLM_API_ERROR", "Synthetic local model failure")
        error.__cause__ = cause
    assert retry_delay(error, 1, delay_seconds=1, max_wait_seconds=30) == 7


@pytest.mark.parametrize("status,attempts", [(400, 1), (401, 1), (429, 3), (503, 3)])
def test_ollama_adapter_http_status_controls_actual_retry_count(status, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "Synthetic failure"})

    async def run():
        async with httpx.AsyncClient(
            base_url="http://localhost:11434/", transport=httpx.MockTransport(handler)
        ) as client:
            model = RetryingGenerator(
                OllamaStructuredGenerator(client, "test-model", 32),
                max_attempts=3,
                delay_seconds=0,
                max_wait_seconds=30,
            )
            with pytest.raises(GenerationError):
                await model.generate(
                    instructions="Test", payload={}, output_type=ExtractionProposal
                )

    asyncio.run(run())
    assert len(calls) == attempts
