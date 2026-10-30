# KURE의 요청·응답 계약, 오류 처리와 실제 그래프 연결을 외부 호출 없이 검증한다.

import asyncio
import copy
import importlib
import json
import math
from hashlib import sha256
from pathlib import Path

import httpx2 as httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr, ValidationError

from app.domains.documents.pipeline.devtools.demo import DemoGenerator, get_demo_graph
from app.domains.documents.pipeline.embedding_texts import (
    EmbeddingTextOptions,
    build_admitted_embedding_texts,
)
from app.domains.documents.pipeline.embeddings.kure import KureEmbeddingClient
from app.domains.documents.pipeline.embeddings.models import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingSpec,
    EmbeddingUsage,
)
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.llm import configured_embedder
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.embed import make_embed_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.verification.models import VerificationOptions

SPEC = EmbeddingSpec(dimensions=256)


def vector(index=0):
    value = (index + 1) / 100
    return [value, math.sqrt(1 - value**2), *([0.0] * 254)]


def response_body(count=1):
    return {
        "object": "list",
        "model": SPEC.model,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector(index)}
            for index in range(count)
        ],
        "usage": {"prompt_tokens": 6, "total_tokens": 6, "completion_tokens": 0},
    }


def invoke_client(handler, texts=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            return await KureEmbeddingClient(transport, SPEC, "test-kure").embed(texts or ["hello"])

    return asyncio.run(run())


def test_http_contract_and_index_mapping_preserve_exact_text():
    texts = ["  한국어 코드\n    run()\n", "second"]
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://kure.kullm.ai/api/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-kure"
        assert json.loads(request.content) == {
            "model": SPEC.model,
            "input": texts,
            "input_type": "document",
            "dimensions": 256,
            "encoding_format": "float",
        }
        body = response_body(2)
        body["data"].reverse()
        return httpx.Response(200, json=body)

    result = invoke_client(handler, texts)
    assert result.vectors == [vector(0), vector(1)] and len(calls) == 1
    assert result.model == SPEC.model and result.dimensions == 256
    assert result.usage.prompt_tokens == 6


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "duplicate",
        "bool_index",
        "dimension",
        "nan",
        "boolean",
        "string",
        "zero",
        "model",
        "usage",
        "object",
        "count",
    ],
)
def test_invalid_responses_never_become_embeddings(bad):
    body = response_body(2)
    if bad == "missing":
        del body["data"][0]["index"]
    elif bad == "duplicate":
        body["data"][1]["index"] = 0
    elif bad == "bool_index":
        body["data"][0]["index"] = False
    elif bad == "dimension":
        body["data"][0]["embedding"].pop()
    elif bad == "nan":
        body["data"][0]["embedding"][0] = float("nan")
    elif bad == "boolean":
        body["data"][0]["embedding"][0] = True
    elif bad == "string":
        body["data"][0]["embedding"][0] = "0.1"
    elif bad == "zero":
        body["data"][0]["embedding"] = [0.0] * 256
    elif bad == "model":
        body["model"] = "another-model"
    elif bad == "usage":
        body["usage"]["prompt_tokens"] = True
    elif bad == "object":
        body["data"][0]["object"] = "message"
    elif bad == "count":
        body["data"].pop()

    def handler(request):
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
        )

    with pytest.raises(EmbeddingError) as caught:
        invoke_client(handler, ["one", "two"])
    assert caught.value.code == (
        "EMBEDDING_MODEL_MISMATCH" if bad == "model" else "EMBEDDING_INVALID_OUTPUT"
    )


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (400, "EMBEDDING_INPUT_TOO_LARGE", False),
        (401, "EMBEDDING_AUTH_ERROR", False),
        (403, "EMBEDDING_AUTH_ERROR", False),
        (404, "EMBEDDING_MODEL_UNAVAILABLE", False),
        (410, "EMBEDDING_MODEL_UNAVAILABLE", False),
        (429, "EMBEDDING_RATE_LIMITED", True),
        (500, "EMBEDDING_API_ERROR", True),
        (503, "EMBEDDING_API_ERROR", True),
        (307, "EMBEDDING_API_ERROR", False),
    ],
)
def test_errors_are_safe_not_retried_or_redirected(status, code, retryable):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={
                "error": {"code": "context_length_exceeded", "message": "test-kure private input"}
            },
            headers={"retry-after": "2", "location": "https://example.com/"},
        )

    with pytest.raises(EmbeddingError) as caught:
        invoke_client(handler)
    error = caught.value
    assert error.code == code and error.retryable is retryable
    assert error.status_code == status and error.retry_after == 2
    assert "test-kure" not in str(error) and "private input" not in str(error)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "kind,code",
    [(httpx.ReadTimeout, "EMBEDDING_TIMEOUT"), (httpx.ConnectError, "EMBEDDING_CONNECTION_ERROR")],
)
def test_transport_failure_not_retried(kind, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise kind("private test-kure", request=request)

    with pytest.raises(EmbeddingError) as caught:
        invoke_client(handler)
    assert caught.value.code == code and caught.value.retryable
    assert len(calls) == 1 and "private" not in str(caught.value)


@pytest.mark.parametrize("texts", [[], [""], ["  "], ["one"] * 9, [False], "text", None])
def test_bad_inputs_make_no_http_calls(texts):
    async def run():
        def forbidden(request):
            pytest.fail("invalid input must not call API")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as transport:
            with pytest.raises(EmbeddingError, match="EMBEDDING_INVALID_INPUT"):
                await KureEmbeddingClient(transport, SPEC, "test-kure").embed(texts)

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, "", "   ", "test key", "test\nkey"])
def test_missing_or_malformed_key_fails_before_client_creation(monkeypatch, value):
    from app.config import settings
    from app.domains.documents.pipeline import llm

    monkeypatch.setattr(settings, "KURE_API_KEY", SecretStr(value) if value is not None else None)

    def forbidden(**kwargs):
        pytest.fail("key validation must precede client creation")

    monkeypatch.setattr(llm.httpx, "AsyncClient", forbidden)

    async def run():
        with pytest.raises(EmbeddingError, match="EMBEDDING_NOT_CONFIGURED"):
            async with configured_embedder():
                pass

    asyncio.run(run())


@pytest.fixture
def admitted_state():
    return asyncio.run(get_demo_graph().ainvoke({}))


def test_stale_or_tampered_text_never_calls_model(admitted_state):
    state = copy.deepcopy(admitted_state)
    text = state["embedding_texts"][0].texts["solution"]
    state["embedding_texts"][0].texts["solution"] = text.model_copy(update={"text": "tampered"})

    class Forbidden:
        async def embed(self, texts):
            pytest.fail("stale input must not call API")

    with pytest.raises(EmbeddingError, match="EMBEDDING_STALE_INPUT"):
        asyncio.run(make_embed_node(Forbidden(), spec=SPEC)(state))


def test_client_interface_can_be_replaced_without_rebuilding_graph_contract(admitted_state):
    spec = EmbeddingSpec(provider="test-provider", model="test-model", dimensions=3)

    class OtherClient:
        async def embed(self, texts):
            return EmbeddingBatch(
                model=spec.model,
                dimensions=3,
                vectors=[[1.0, 0.0, 0.0] for _ in texts],
                usage=EmbeddingUsage(prompt_tokens=1, total_tokens=1),
            )

    result = asyncio.run(make_embed_node(OtherClient(), spec=spec)(admitted_state))
    assert result["embeddings"][0].provider == "test-provider"
    assert result["embeddings"][0].model == "test-model"
    assert len(result["embeddings"][0].vectors) == 4


def test_empty_or_all_skipped_input_does_not_create_client(monkeypatch, admitted_state):
    module = importlib.import_module("app.domains.documents.pipeline.nodes.embed")

    def forbidden():
        pytest.fail("empty input must not create client")

    monkeypatch.setattr(module, "configured_embedder", forbidden)
    assert asyncio.run(module.embed({})) == {"embeddings": [], "embedding_usage": []}
    state = copy.deepcopy(admitted_state)
    state["embedding_text_options"] = EmbeddingTextOptions(max_chars=1)
    state["embedding_texts"] = build_admitted_embedding_texts(
        state["verified_drafts"],
        state["verification_results"],
        options=state["embedding_text_options"],
    )
    assert asyncio.run(module.embed(state)) == {"embeddings": [], "embedding_usage": []}


def test_multiple_cards_batch_mapping_and_no_partial_success_on_failure(admitted_state):
    state = copy.deepcopy(admitted_state)
    original = state["verified_drafts"][0]
    verdict = state["verification_results"][0]
    state["verified_drafts"] = [
        original.model_copy(update={"experience_id": f"experience-{index}"}, deep=True)
        for index in range(3)
    ]
    state["verification_results"] = [
        verdict.model_copy(update={"experience_id": draft.experience_id})
        for draft in state["verified_drafts"]
    ]
    state["embedding_texts"] = build_admitted_embedding_texts(
        state["verified_drafts"], state["verification_results"], options=EmbeddingTextOptions()
    )

    async def run(fail):
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            if fail and len(calls) == 2:
                return httpx.Response(503, json={"error": {"code": "no_worker_available"}})
            return httpx.Response(200, json=response_body(len(body["input"])))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            node = make_embed_node(
                KureEmbeddingClient(transport, SPEC, "test-kure"), spec=SPEC, batch_size=8
            )
            if fail:
                with pytest.raises(EmbeddingError):
                    await node(state)
                assert state["embeddings"] == []
            else:
                result = await node(state)
                assert len(result["embeddings"]) == 3 and len(result["embedding_usage"]) == 2
                for index, (card, plan) in enumerate(
                    zip(result["embeddings"], state["embedding_texts"], strict=True)
                ):
                    assert card.experience_id == plan.experience_id
                    assert card.model == SPEC.model and card.dimensions == 256
                    assert card.template_version == plan.template_version
                    for view_index, (view, text) in enumerate(plan.texts.items()):
                        assert card.vectors[view] == vector((index * 4 + view_index) % 8)
                        assert card.input_hashes[view] == sha256(text.text.encode()).hexdigest()
            assert [len(call["input"]) for call in calls] == [8, 4]

    asyncio.run(run(False))
    asyncio.run(run(True))


def test_actual_graph_calls_configured_kure_after_final_retry(monkeypatch):
    from app.config import settings
    from app.domains.documents.pipeline import llm

    monkeypatch.setattr(settings, "KURE_API_KEY", SecretStr("  test-kure  "))
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 256)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://localhost:11434")
    transport_class = httpx.AsyncClient
    calls = []

    def handler(request):
        assert str(request.url) == "https://kure.kullm.ai/api/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-kure"
        body = json.loads(request.content)
        calls.append(body)
        assert "Redis" not in "\n".join(body["input"])
        return httpx.Response(200, json=response_body(len(body["input"])))

    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: transport_class(**kwargs, transport=httpx.MockTransport(handler)),
    )
    generator = DemoGenerator()
    graph = build_graph(
        checkpointer=InMemorySaver(),
        interrupt_before=["embed"],
        extract_node=make_extract_node(generator),
        draft_node=make_draft_node(generator),
        verify_node=make_verify_node(
            options=VerificationOptions(llm_enabled=True), generator=generator
        ),
    )
    path = Path("app/domains/documents/pipeline/devtools/demo.md").resolve()

    async def run():
        config = {"configurable": {"thread_id": "kure-resume"}}
        await graph.ainvoke(
            {
                "sources": [
                    {
                        "document_id": "4c10a229-9b3a-40bc-8611-e2f787540f03",
                        "original_filename": path.name,
                        "path": str(path),
                    }
                ]
            },
            config,
        )
        assert not calls and graph.get_state(config).next == ("embed",)
        result = await graph.ainvoke(None, config)
        await graph.ainvoke(None, config)
        assert len(calls) == 1
        return result

    result = asyncio.run(run())
    assert len(calls) == 1 and len(calls[0]["input"]) == 4
    assert len(result["embeddings"]) == 1 and len(result["verification_history"]) == 1
    assert result["embeddings"][0].model == SPEC.model


@pytest.mark.parametrize(
    "field,value",
    [
        ("EMBEDDING_MODEL", "kure-v3"),
        ("EMBEDDING_DIMENSIONS", 100),
        ("EMBEDDING_BATCH_SIZE", 9),
        ("EMBEDDING_TIMEOUT_SECONDS", 0),
    ],
)
def test_invalid_embedding_settings_are_rejected(field, value):
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_numeric_embedding_environment_values_load_as_numbers(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "512")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "4")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", "15")
    config = Settings(_env_file=None)
    assert config.EMBEDDING_DIMENSIONS == 512
    assert config.EMBEDDING_BATCH_SIZE == 4
    assert config.EMBEDDING_TIMEOUT_SECONDS == 15
