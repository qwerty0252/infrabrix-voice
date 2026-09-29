"""An ``LLMProvider`` for any OpenAI-compatible chat-completions API.

OpenRouter, Google Gemini (``/v1beta/openai``), and Groq all speak the same
wire format, so one implementation serves all three — the base URL, key, and
default model come from :class:`ProviderConfig`.

Responsibilities: send messages, tool calling, structured output,
timeouts, retry handling, model configuration, usage recording, error
normalization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from app.errors import AgentProviderError
from app.providers.llm.base import LLMProvider
from app.providers.llm.models import (
    CompletionResult,
    Message,
    TokenUsage,
    ToolCall,
    ToolSpec,
)

logger = logging.getLogger(__name__)

TSchema = TypeVar("TSchema", bound=BaseModel)

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
# Providers that reliably honour response_format=json_schema. Others use the
# prompt-embedded-JSON fallback directly.
_SUPPORTS_JSON_SCHEMA = {"openai", "openrouter", "groq"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: SecretStr
    default_model: str
    timeout_seconds: float = 60.0
    max_retries: int = 2


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, config: ProviderConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.name = config.name
        self._cfg = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "X-Title": "InfraBrix Voice",
            },
        )

    # -- public API ------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        payload: dict[str, Any] = {
            "model": model or self._cfg.default_model,
            "messages": [_encode_message(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
        return await self._request(payload)

    async def complete_structured(
        self,
        messages: list[Message],
        schema: type[TSchema],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> tuple[TSchema, CompletionResult]:
        json_schema = schema.model_json_schema()
        base: dict[str, Any] = {
            "model": model or self._cfg.default_model,
            "messages": [_encode_message(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            base["max_tokens"] = max_tokens

        if self._cfg.name in _SUPPORTS_JSON_SCHEMA:
            strict_payload = {
                **base,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "strict": True,
                        "schema": json_schema,
                    },
                },
            }
            try:
                result = await self._request(strict_payload)
                return self._parse_structured(result, schema), result
            except AgentProviderError:
                logger.info("json_schema rejected by %s; using prompt fallback", self._cfg.name)

        instruction = (
            "Reply with ONLY a JSON object matching this schema "
            "(no prose, no code fence):\n"
            f"{json.dumps(json_schema)}"
        )
        prompt_payload = {
            **base,
            "messages": [*base["messages"], {"role": "system", "content": instruction}],
        }
        result = await self._request(prompt_payload)
        return self._parse_structured(result, schema), result

    def _parse_structured(self, result: CompletionResult, schema: type[TSchema]) -> TSchema:
        raw = (result.message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise AgentProviderError(
                f"LLM response did not match {schema.__name__}",
                code="agent_structured_output_invalid",
                details={"errors": exc.errors(include_url=False)},
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- internals ------------------------------------------------------

    async def _request(self, payload: dict[str, Any]) -> CompletionResult:
        last_exc: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            start = time.perf_counter()
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("%s transport error (attempt %d): %s", self._cfg.name, attempt, exc)
                await self._backoff(attempt)
                continue

            latency_ms = int((time.perf_counter() - start) * 1000)
            if response.status_code in _RETRYABLE_STATUS:
                last_exc = AgentProviderError(
                    f"{self._cfg.name} returned {response.status_code}",
                    code="agent_provider_retryable",
                )
                await self._backoff(attempt)
                continue
            if response.status_code >= 400:
                raise AgentProviderError(
                    f"{self._cfg.name} request failed ({response.status_code}): "
                    f"{_error_detail(response)}",
                    code="agent_provider_error",
                    details={"status": response.status_code},
                )
            return _decode_completion(response.json(), self._cfg.name, latency_ms=latency_ms)

        raise AgentProviderError(
            f"{self._cfg.name} request failed after retries",
            code="agent_provider_unavailable",
        ) from last_exc

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(min(2**attempt, 8) * 0.5)


class UnconfiguredProvider(LLMProvider):
    """Returned when the selected provider has no API key.

    Fails on first use with a clear message rather than at dependency-resolution
    time, so the agent turn loop can stream the error to the UI.
    """

    name = "unconfigured"

    def __init__(self, message: str) -> None:
        self._message = message

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        raise AgentProviderError(self._message, code="agent_provider_unconfigured")

    async def complete_structured(self, *args: Any, **kwargs: Any) -> Any:
        raise AgentProviderError(self._message, code="agent_provider_unconfigured")


# --- wire encoding / decoding ---------------------------------------------


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("error", {}).get("message") or body.get("error") or body)[:300]
    except Exception:
        return response.text[:300]


def _encode_message(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        out["content"] = message.content
    if message.tool_call_id:
        out["tool_call_id"] = message.tool_call_id
    if message.name:
        out["name"] = message.name
    if message.tool_calls:
        encoded_calls: list[dict[str, Any]] = []
        for tc in message.tool_calls:
            call: dict[str, Any] = {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            # Gemini 3 rejects follow-up requests whose function calls dropped the
            # thought signature it handed back; replay it untouched.
            if tc.provider_meta:
                call["extra_content"] = tc.provider_meta
            encoded_calls.append(call)
        out["tool_calls"] = encoded_calls
    return out


def _encode_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _decode_completion(body: dict[str, Any], provider: str, *, latency_ms: int) -> CompletionResult:
    try:
        choice = body["choices"][0]
        raw_message = choice["message"]
    except (KeyError, IndexError) as exc:
        raise AgentProviderError(
            f"{provider} response was malformed", code="agent_provider_error"
        ) from exc

    tool_calls: list[ToolCall] = []
    for tc in raw_message.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(
            ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=args,
                provider_meta=tc.get("extra_content"),
            )
        )

    usage_body = body.get("usage") or {}
    return CompletionResult(
        message=Message(
            role="assistant", content=raw_message.get("content"), tool_calls=tool_calls
        ),
        finish_reason=_normalize_finish(choice.get("finish_reason")),
        model=body.get("model", "unknown"),
        usage=TokenUsage(
            prompt_tokens=usage_body.get("prompt_tokens", 0),
            completion_tokens=usage_body.get("completion_tokens", 0),
            total_tokens=usage_body.get("total_tokens", 0),
        ),
        latency_ms=latency_ms,
        provider=provider,
    )


def _normalize_finish(value: str | None) -> Any:
    mapping = {
        "stop": "stop",
        "length": "length",
        "tool_calls": "tool_calls",
        "function_call": "tool_calls",
        "content_filter": "content_filter",
    }
    return mapping.get(value or "", "stop")
