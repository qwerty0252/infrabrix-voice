"""A deterministic, offline ``LLMProvider`` for the demo.

It maps the latest request to one typed tool call by keyword, then phrases the
tool's ``headline`` as the reply. It exists so the voice demo runs with only an
AssemblyAI key. It goes through the exact same runtime, policies, approval gate
and audit trail as a real model; set ``LLM_PROVIDER`` to use one.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, TypeVar

from pydantic import BaseModel

from app.errors import AgentProviderError
from app.providers.llm.base import LLMProvider
from app.providers.llm.models import CompletionResult, Message, TokenUsage, ToolCall, ToolSpec

TSchema = TypeVar("TSchema", bound=BaseModel)

_SERVICES = {
    "checkout": "checkout-api",
    "payment": "checkout-api",
    "storefront": "storefront",
    "website": "storefront",
    "frontend": "storefront",
    "database": "orders-db",
    "postgres": "orders-db",
    "orders": "orders-db",
}

# Ordered: the first matching intent wins.
_INTENTS: list[tuple[str, tuple[str, ...]]] = [
    ("rollback_release", ("rollback", "revert", "undo the deploy")),
    ("get_cost_summary", ("cost", "spend", "spent", "bill", "budget", "money", "expensive")),
    ("get_recent_errors", ("log", "error message", "exception", "stack")),
    ("get_service_metrics", ("latency", "metric", "error rate", "traffic", "cpu")),
    (
        "diagnose_incident",
        (
            "wrong",
            "broken",
            "down",
            "failing",
            "incident",
            "outage",
            "why",
            "diagnos",
            "slow",
            "errors",
            "issue",
            "problem",
            "fix",
        ),
    ),
    ("list_releases", ("release", "deploy", "version", "shipped")),
    ("get_project_overview", ("status", "health", "overview", "how are", "how is", "services")),
]

_ROLL_BACK = re.compile(r"\broll(ing)?\b.{0,40}\bback\b", re.I)
_GREETING = re.compile(r"^\s*(hi|hey|hello|yo|good (morning|afternoon|evening))\b", re.I)


def _service(text: str) -> str | None:
    lowered = text.lower()
    return next((svc for word, svc in _SERVICES.items() if word in lowered), None)


def plan_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    lowered = text.lower()
    for tool, words in _INTENTS:
        rolls_back = tool == "rollback_release" and _ROLL_BACK.search(text)
        if rolls_back or any(w in lowered for w in words):
            service = _service(text)
            if tool == "rollback_release":
                args: dict[str, Any] = {"service": service or "checkout-api"}
                version = re.search(r"\bv(?:ersion)?\s*(\d+)\b", lowered)
                if version:
                    args["to_version"] = f"v{version.group(1)}"
                return tool, args
            if tool in {"get_recent_errors", "get_service_metrics", "list_releases"}:
                return tool, {"service": service or "checkout-api"}
            if tool == "diagnose_incident":
                return tool, ({"service": service} if service else {})
            return tool, {}
    if _GREETING.match(text):
        return None
    return "get_project_overview", {}


def _reply_from_tool(tool: str | None, payload: dict[str, Any]) -> str:
    if "error" in payload:
        return f"I couldn't do that: {payload['error']}"
    headline = str(payload.get("headline") or "Done.")
    if tool == "diagnose_incident" and payload.get("recommendation"):
        rec = payload["recommendation"]
        return (
            f"{headline} If you want, I can roll {rec['service']} back to "
            f"{rec['to_version']}. You'll approve it on screen before anything changes."
        )
    return headline


class ScriptedLLMProvider(LLMProvider):
    name = "scripted"

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        last_user = max(i for i, m in enumerate(messages) if m.role == "user")
        after = messages[last_user + 1 :]
        tool_results = [m for m in after if m.role == "tool"]
        if tool_results:
            called = next((m.tool_calls[0].name for m in reversed(after) if m.tool_calls), None)
            try:
                payload = json.loads(tool_results[-1].content or "{}")
            except json.JSONDecodeError:
                payload = {}
            return self._text(_reply_from_tool(called, payload), model)

        available = {t.name for t in tools or []}
        planned = plan_tool_call(messages[last_user].content or "")
        if planned is None or planned[0] not in available:
            return self._text(
                "Hi, I'm Brix. Ask me how production is doing, what's broken, "
                "or what you're spending this month.",
                model,
            )
        name, args = planned
        call = ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)
        return CompletionResult(
            message=Message(role="assistant", content=None, tool_calls=[call]),
            finish_reason="tool_calls",
            model=model or "scripted",
            usage=TokenUsage(),
            provider=self.name,
        )

    async def complete_structured(self, *args: Any, **kwargs: Any) -> Any:
        raise AgentProviderError("The scripted planner does not support structured output")

    def _text(self, content: str, model: str | None) -> CompletionResult:
        return CompletionResult(
            message=Message(role="assistant", content=content),
            finish_reason="stop",
            model=model or "scripted",
            usage=TokenUsage(),
            provider=self.name,
        )
