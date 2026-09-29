"""Provider-neutral LLM data types.

These types are the contract between the agent harness and any
:class:`~app.providers.llm.base.LLMProvider`. They are deliberately close to
the OpenAI/OpenRouter chat shape (the de-facto standard) but carry no
provider-specific fields.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Opaque provider round-trip data (e.g. Gemini 3 thought signatures). The
    # harness never inspects it; it is echoed back verbatim on the assistant
    # turn so the next request stays valid.
    provider_meta: dict[str, Any] | None = None


class Message(BaseModel):
    """One turn in a conversation."""

    role: Role
    content: str | None = None
    # assistant turns may carry tool calls; tool turns carry a tool_call_id
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    """A tool definition advertised to the model (JSON-Schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any]


class TokenUsage(BaseModel):
    """Token accounting for one provider call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CompletionResult(BaseModel):
    """The normalized result of a chat completion."""

    message: Message
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "error"]
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    provider: str = "openrouter"
