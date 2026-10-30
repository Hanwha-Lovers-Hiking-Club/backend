# LLM/임베딩 클라이언트 생성을 한 곳에 모은다 (모델 교체, temperature 등 설정 관리).

import json
from contextlib import asynccontextmanager
from typing import Literal, Protocol, TypeVar

import httpx2 as httpx
from anthropic import APIError as AnthropicAPIError
from anthropic import AsyncAnthropic, transform_schema
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

OutputT = TypeVar("OutputT", bound=BaseModel)
GenerationStage = Literal["extract", "draft", "verify"]


class GenerationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StructuredGenerator(Protocol):
    async def generate(
        self, *, instructions: str, payload: dict, output_type: type[OutputT]
    ) -> OutputT: ...


async def generate_structured(
    generator: StructuredGenerator,
    *,
    instructions: str,
    payload: dict,
    output_type: type[OutputT],
    max_input_chars: int,
) -> OutputT:
    # Bound the serialized request, including metadata, without silently truncating sources.
    if len(instructions) + len(json.dumps(payload, ensure_ascii=False)) > max_input_chars:
        raise GenerationError("INPUT_TOO_LARGE", "Input exceeds the configured character limit")
    value = await generator.generate(
        instructions=instructions, payload=payload, output_type=output_type
    )
    # Revalidate even model instances supplied by another implementation/test double.
    return output_type.model_validate(value.model_dump() if isinstance(value, BaseModel) else value)


def output_schema(output_type: type[BaseModel]) -> dict:
    """Ask providers for explicit values, including null/unknown and empty arrays."""
    schema = output_type.model_json_schema()

    def require_properties(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
            for child in node.values():
                require_properties(child)
        elif isinstance(node, list):
            for child in node:
                require_properties(child)

    require_properties(schema)
    return schema


class OpenAIStructuredGenerator:
    def __init__(self, client: AsyncOpenAI, model: str, max_output_tokens: int):
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens

    async def generate(
        self, *, instructions: str, payload: dict, output_type: type[OutputT]
    ) -> OutputT:
        try:
            response = await self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": output_type.__name__,
                        "strict": True,
                        "schema": output_schema(output_type),
                    }
                },
                store=False,
                truncation="disabled",
                max_output_tokens=self.max_output_tokens,
            )
        except OpenAIAPIError as exc:
            raise GenerationError(
                "LLM_API_ERROR", "OpenAI structured model request failed"
            ) from exc
        output = getattr(response, "output", None)
        if not isinstance(output, list):
            raise GenerationError("LLM_INVALID_OUTPUT", "Invalid model response envelope")
        if any(part.type == "refusal" for item in output for part in getattr(item, "content", [])):
            raise GenerationError("LLM_REFUSED", "The model refused this request")
        if response.status != "completed":
            raise GenerationError("LLM_INCOMPLETE", "The model response did not complete")
        texts = []
        for item in output:
            if item.type == "reasoning":
                continue
            if item.type != "message" or item.role != "assistant" or item.status != "completed":
                raise GenerationError(
                    "LLM_INVALID_OUTPUT", "Expected a completed assistant message"
                )
            for part in item.content:
                if part.type != "output_text":
                    raise GenerationError(
                        "LLM_INVALID_OUTPUT", "Expected structured assistant text"
                    )
                texts.append(part.text)
        if not texts or not any(text.strip() for text in texts):
            raise GenerationError("LLM_EMPTY", "No structured model output was returned")
        if len(texts) != 1:
            raise GenerationError("LLM_INVALID_OUTPUT", "Expected one structured text block")
        return validate_output(output_type, texts[0])


class AnthropicStructuredGenerator:
    def __init__(self, client: AsyncAnthropic, model: str, max_output_tokens: int):
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens

    async def generate(
        self, *, instructions: str, payload: dict, output_type: type[OutputT]
    ) -> OutputT:
        try:
            # Inspect stop_reason before parsing: refusals/truncated output may not be JSON.
            response = await self.client.messages.create(
                model=self.model,
                system=instructions,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": transform_schema(output_schema(output_type)),
                    }
                },
                max_tokens=self.max_output_tokens,
            )
        except AnthropicAPIError as exc:
            raise GenerationError("LLM_API_ERROR", "Structured model request failed") from exc
        if response.stop_reason == "refusal":
            raise GenerationError("LLM_REFUSED", "The model refused this request")
        if response.stop_reason != "end_turn":
            raise GenerationError("LLM_INCOMPLETE", "The model response did not complete")
        texts = [part.text for part in response.content if part.type == "text"]
        if not texts or not any(text.strip() for text in texts):
            raise GenerationError("LLM_EMPTY", "No structured model output was returned")
        if len(texts) != 1:
            raise GenerationError("LLM_INVALID_OUTPUT", "Expected one structured text block")
        return validate_output(output_type, texts[0])


def validate_output(output_type: type[OutputT], text: str) -> OutputT:
    # Provider-side schema constraints never replace the original Pydantic validation.
    try:
        return output_type.model_validate_json(text)
    except ValidationError as exc:
        raise GenerationError(
            "LLM_INVALID_OUTPUT", "Model returned invalid structured output"
        ) from exc


class OllamaStructuredGenerator:
    def __init__(self, client: httpx.AsyncClient, model: str, max_output_tokens: int):
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens

    async def generate(
        self, *, instructions: str, payload: dict, output_type: type[OutputT]
    ) -> OutputT:
        try:
            response = await self.client.post(
                "api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    "stream": False,
                    "format": output_schema(output_type),
                    "options": {"num_predict": self.max_output_tokens, "temperature": 0},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GenerationError("LLM_API_ERROR", "Local model request failed") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise GenerationError("LLM_INVALID_OUTPUT", "Invalid local model response") from exc
        if not isinstance(body, dict):
            raise GenerationError("LLM_INVALID_OUTPUT", "Expected a model response object")
        if body.get("error"):
            raise GenerationError("LLM_API_ERROR", "Local model reported an error")
        if body.get("done") is not True or body.get("done_reason") != "stop":
            raise GenerationError("LLM_INCOMPLETE", "Local model response did not complete")
        message = body.get("message")
        if not isinstance(message, dict) or message.get("tool_calls"):
            raise GenerationError("LLM_INVALID_OUTPUT", "Expected structured assistant text")
        text = message.get("content")
        if not isinstance(text, str):
            raise GenerationError("LLM_INVALID_OUTPUT", "Expected structured assistant text")
        if not text.strip():
            raise GenerationError("LLM_EMPTY", "No structured model output was returned")
        return validate_output(output_type, text)


@asynccontextmanager
async def configured_generator(stage: GenerationStage):
    from app.config import settings

    if stage not in ("extract", "draft", "verify"):
        raise GenerationError("LLM_NOT_CONFIGURED", "Unknown generation stage")
    model = {
        "extract": settings.EXTRACT_MODEL,
        "draft": settings.DRAFT_MODEL,
        "verify": settings.VERIFY_MODEL,
    }[stage]
    if not model or not model.strip():
        raise GenerationError("LLM_NOT_CONFIGURED", "Set the model for this generation stage")
    if settings.LLM_PROVIDER == "openai":
        key = settings.OPENAI_API_KEY
        if key is None or not key.get_secret_value().strip():
            raise GenerationError("LLM_NOT_CONFIGURED", "Set OPENAI_API_KEY")
        async with AsyncOpenAI(
            api_key=key.get_secret_value().strip(),
            base_url="https://api.openai.com/v1",
            timeout=settings.DOCUMENT_LLM_TIMEOUT_SECONDS,
            max_retries=0,
        ) as client:
            yield OpenAIStructuredGenerator(
                client, model.strip(), settings.DOCUMENT_LLM_MAX_OUTPUT_TOKENS
            )
        return
    if settings.LLM_PROVIDER == "ollama":
        async with httpx.AsyncClient(
            base_url=str(settings.LLM_BASE_URL).rstrip("/") + "/",
            timeout=settings.DOCUMENT_LLM_TIMEOUT_SECONDS,
        ) as client:
            yield OllamaStructuredGenerator(
                client, model.strip(), settings.DOCUMENT_LLM_MAX_OUTPUT_TOKENS
            )
        return
    if settings.LLM_PROVIDER != "anthropic":
        raise GenerationError("LLM_NOT_CONFIGURED", "Unsupported LLM provider")
    key = settings.ANTHROPIC_API_KEY
    if key is None or not key.get_secret_value().strip():
        raise GenerationError("LLM_NOT_CONFIGURED", "Set ANTHROPIC_API_KEY")
    async with AsyncAnthropic(
        api_key=key.get_secret_value().strip(),
        timeout=settings.DOCUMENT_LLM_TIMEOUT_SECONDS,
        max_retries=0,
    ) as client:
        yield AnthropicStructuredGenerator(
            client, model.strip(), settings.DOCUMENT_LLM_MAX_OUTPUT_TOKENS
        )


class ConfiguredGenerator:
    """Lazy default. Empty/insufficient input does not require keys or create a client."""

    def __init__(self, stage: GenerationStage):
        self.stage = stage

    async def generate(
        self, *, instructions: str, payload: dict, output_type: type[OutputT]
    ) -> OutputT:
        async with configured_generator(self.stage) as generator:
            return await generator.generate(
                instructions=instructions, payload=payload, output_type=output_type
            )


@asynccontextmanager
async def configured_embedder():
    from app.config import settings
    from app.domains.documents.pipeline.embeddings.kure import (
        KureEmbeddingClient,
        validate_kure_key,
    )
    from app.domains.documents.pipeline.embeddings.models import EmbeddingSpec

    key = validate_kure_key(settings.KURE_API_KEY)
    spec = EmbeddingSpec(model=settings.EMBEDDING_MODEL, dimensions=settings.EMBEDDING_DIMENSIONS)
    async with httpx.AsyncClient(
        timeout=settings.EMBEDDING_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        yield KureEmbeddingClient(client, spec, key)
