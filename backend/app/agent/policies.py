"""PolicyEngine + PermissionEngine.

READ / PLAN / WRITE_LOW_RISK run automatically. WRITE_INFRASTRUCTURE and
DESTRUCTIVE never run inside an agent turn: the call is parked and a person
decides it in the UI. The agent never approves its own request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.models import ToolRisk


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class PolicyEngine:
    """Org/project policy checks that run before a tool executes."""

    def check(self, *, tool: str, risk: ToolRisk) -> PolicyDecision:
        # The demo has no project-level deny rules; this is where they go.
        return PolicyDecision(allowed=True, reason="no policy restriction")


class PermissionEngine:
    """Maps risk level to an execution decision."""

    _AUTO: ClassVar[set[ToolRisk]] = {
        ToolRisk.READ,
        ToolRisk.PLAN,
        ToolRisk.WRITE_LOW_RISK,
    }

    def decide(self, *, risk: ToolRisk, policy: PolicyDecision) -> PolicyDecision:
        if not policy.allowed:
            return policy
        if risk in self._AUTO and not policy.requires_approval:
            return PolicyDecision(allowed=True, reason=f"{risk.value} runs automatically")
        return PolicyDecision(
            allowed=True,
            requires_approval=True,
            reason=f"{risk.value} requires human approval",
        )


class ApprovalRequiredError(Exception):
    """Raised when a tool call is parked pending a human decision.

    ``action_id``/``tool`` identify the parked ``agent_actions`` row so a UI can
    render an approve/deny card for it.
    """

    def __init__(
        self, message: str = "", *, action_id: uuid.UUID | None = None, tool: str | None = None
    ) -> None:
        super().__init__(message)
        self.action_id = action_id
        self.tool = tool
