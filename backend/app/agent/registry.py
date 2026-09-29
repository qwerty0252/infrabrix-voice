"""Typed tool interface + registry."""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ToolRisk, User
from app.providers.llm.base import LLMProvider
from app.providers.llm.models import ToolSpec


@dataclass
class ToolContext:
    """Everything a tool may touch. Resolved on the server; never carries secret values."""

    session: AsyncSession
    project: Project
    user: User
    llm: LLMProvider
    run_id: uuid.UUID


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    summary: str = ""

    @classmethod
    def success(cls, data: dict[str, Any], summary: str = "") -> ToolResult:
        return cls(ok=True, data=data, summary=summary or "ok")

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        return cls(ok=False, error=error, summary=error)


class Tool(abc.ABC):
    name: str
    description: str
    risk: ToolRisk
    Args: type[BaseModel]

    @abc.abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> ToolResult: ...

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.Args.model_json_schema(),
        )


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]
