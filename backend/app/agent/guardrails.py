"""EvaluationGuardrails — pre/post checks on model and tool traffic."""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # OpenAI-style
    re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),  # Stripe live
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),  # GitHub PAT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

MAX_STEPS = 12
MAX_TOKENS_PER_RUN = 120_000


class GuardrailTrippedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def scan_for_secrets(payload: Any, *, where: str) -> None:
    text = _stringify(payload)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise GuardrailTrippedError(f"secret-shaped value detected in {where}")


def check_step_budget(step: int) -> None:
    if step >= MAX_STEPS:
        raise GuardrailTrippedError(f"step budget exhausted ({MAX_STEPS})")


def check_token_budget(total_tokens: int) -> None:
    if total_tokens > MAX_TOKENS_PER_RUN:
        raise GuardrailTrippedError(f"token budget exceeded ({MAX_TOKENS_PER_RUN})")


class RepeatedCallGuard:
    """Trips when the model asks for the exact same tool + args three times."""

    def __init__(self, limit: int = 3) -> None:
        self._limit = limit
        self._seen: dict[str, int] = {}

    def record(self, tool: str, args: dict[str, Any]) -> None:
        key = f"{tool}:{sorted(args.items())}"
        self._seen[key] = self._seen.get(key, 0) + 1
        if self._seen[key] >= self._limit:
            raise GuardrailTrippedError(f"tool {tool} called with identical args {self._limit}x")


def _stringify(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return " ".join(_stringify(v) for v in payload.values())
    if isinstance(payload, list | tuple):
        return " ".join(_stringify(v) for v in payload)
    return str(payload)
