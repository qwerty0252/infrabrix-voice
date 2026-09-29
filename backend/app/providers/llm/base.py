"""The ``LLMProvider`` abstraction.

The agent is not coupled to one model or one vendor. Every part of the
system that reasons with an LLM does so through this interface. Implementations:
an OpenAI-compatible provider and an offline scripted planner.
"""

from __future__ import annotations

import abc
from typing import TypeVar

from pydantic import BaseModel

from app.providers.llm.models import CompletionResult, Message, ToolSpec

TSchema = TypeVar("TSchema", bound=BaseModel)


class LLMProvider(abc.ABC):
    """Vendor-neutral chat + tool-calling + structured-output interface."""

    name: str = "abstract"

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Run one chat completion, optionally advertising ``tools``.

        Raises:
            app.errors.AgentProviderError: On any provider failure after
                retries are exhausted.
        """

    @abc.abstractmethod
    async def complete_structured(
        self,
        messages: list[Message],
        schema: type[TSchema],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> tuple[TSchema, CompletionResult]:
        """Run a completion constrained to ``schema`` and return the parsed model.

        Used wherever the model produces machine-consumed output. Never parse
        free text with regex.

        Raises:
            app.errors.AgentProviderError: On provider failure or if the
                response cannot be coerced to ``schema``.
        """
