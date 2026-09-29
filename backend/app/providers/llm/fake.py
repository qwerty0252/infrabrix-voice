"""A scripted LLMProvider for tests and offline demos.

Returns pre-baked completions in order. Each turn is either free text, a set of
tool calls, or (for ``complete_structured``) a dict validated against the schema.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from app.providers.llm.base import LLMProvider
from app.providers.llm.models import CompletionResult, Message, TokenUsage, ToolCall, ToolSpec

TSchema = TypeVar("TSchema", bound=BaseModel)


@dataclass
class FakeTurn:
    content: str | None = None
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    structured: dict[str, Any] | None = None


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(self, script: list[FakeTurn] | None = None) -> None:
        self._script = list(script or [])
        self._i = 0

    def _next(self) -> FakeTurn:
        if self._i >= len(self._script):
            return FakeTurn(content="(fake LLM: no more scripted turns)")
        turn = self._script[self._i]
        self._i += 1
        return turn

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        turn = self._next()
        tool_calls = [
            ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)
            for name, args in turn.tool_calls
        ]
        return CompletionResult(
            message=Message(role="assistant", content=turn.content, tool_calls=tool_calls),
            finish_reason="tool_calls" if tool_calls else "stop",
            model=model or "fake/model",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            provider="fake",
        )

    async def complete_structured(
        self,
        messages: list[Message],
        schema: type[TSchema],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> tuple[TSchema, CompletionResult]:
        turn = self._next()
        parsed = schema.model_validate(turn.structured or {})
        result = CompletionResult(
            message=Message(role="assistant", content=parsed.model_dump_json()),
            finish_reason="stop",
            model=model or "fake/model",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=40, total_tokens=60),
            provider="fake",
        )
        return parsed, result
