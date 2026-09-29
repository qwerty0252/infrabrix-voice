"""AgentRuntime: the one turn loop behind text chat and voice.

``run()`` is an async generator of stream events. ``run_result()`` collects
those events into the channel-independent result the voice bridge speaks.
Both channels share context, tools, policies, approvals, and audit.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.audit import AuditLogger
from app.agent.guardrails import (
    GuardrailTrippedError,
    RepeatedCallGuard,
    check_step_budget,
    check_token_budget,
    scan_for_secrets,
)
from app.agent.policies import ApprovalRequiredError, PermissionEngine, PolicyEngine
from app.agent.prompts import SYSTEM_PROMPT, build_context_block
from app.agent.registry import ToolContext, ToolRegistry
from app.agent.voice_approvals import INLINE_CONFIRMABLE_TOOLS
from app.errors import AgentProviderError
from app.models import (
    AgentActionStatus,
    AgentRunStatus,
    Conversation,
    MessageChannel,
    MessageRole,
    Project,
    User,
)
from app.providers.llm.base import LLMProvider
from app.providers.llm.models import Message
from app.repositories import AgentRunRepository, ConversationRepository

logger = logging.getLogger(__name__)

StreamEvent = dict[str, Any]

_LLM_ROLE = {MessageRole.USER: "user", MessageRole.ASSISTANT: "assistant"}


class AgentRuntime:
    def __init__(
        self,
        *,
        session: AsyncSession,
        llm: LLMProvider,
        registry: ToolRegistry,
        model: str,
    ) -> None:
        self._s = session
        self._llm = llm
        self._registry = registry
        self._model = model
        self._policy = PolicyEngine()
        self._permission = PermissionEngine()

    async def run(
        self,
        *,
        conversation: Conversation,
        project: Project,
        user: User,
        user_message: str,
        message_channel: MessageChannel = MessageChannel.TEXT,
    ) -> AsyncIterator[StreamEvent]:
        convos = ConversationRepository(self._s)
        runs = AgentRunRepository(self._s)
        audit = AuditLogger(self._s)
        repeated = RepeatedCallGuard()

        run = await runs.create(
            conversation_id=conversation.id, model=self._model, provider=self._llm.name
        )
        history = await convos.messages(conversation.id)
        await convos.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            channel=message_channel,
            content=user_message,
            author_id=user.id,
        )
        await self._s.commit()
        yield {"type": "run_started", "run_id": str(run.id)}

        ctx = ToolContext(session=self._s, project=project, user=user, llm=self._llm, run_id=run.id)
        messages = [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="system", content=build_context_block(project)),
            *[
                Message(role=_LLM_ROLE[row.role], content=row.content)
                for row in history
                if row.content
            ],
            Message(role="user", content=user_message),
        ]

        total_in = total_out = latency = steps = 0
        status = AgentRunStatus.SUCCEEDED
        stop_reason = "completed"
        final_text = ""

        try:
            while True:
                check_step_budget(steps)
                steps += 1
                started = time.perf_counter()
                completion = await self._llm.complete(
                    messages, model=self._model, tools=self._registry.specs()
                )
                latency += int((time.perf_counter() - started) * 1000)
                total_in += completion.usage.prompt_tokens
                total_out += completion.usage.completion_tokens
                check_token_budget(total_in + total_out)

                assistant = completion.message
                messages.append(assistant)

                if not assistant.tool_calls:
                    final_text = assistant.content or ""
                    scan_for_secrets(final_text, where="assistant reply")
                    break

                for call in assistant.tool_calls:
                    async for event in self._run_tool(call, ctx, audit, repeated, messages):
                        yield event
                await self._s.commit()
        except ApprovalRequiredError as exc:
            status = AgentRunStatus.AWAITING_APPROVAL
            stop_reason = "approval_required"
            final_text = (
                f"I've prepared the {(exc.tool or 'change').replace('_', ' ')}. "
                "Nothing has changed yet. Approve it on screen when you're ready."
            )
            yield {
                "type": "approval_required",
                "detail": str(exc),
                "action_id": str(exc.action_id) if exc.action_id else None,
                "tool": exc.tool,
            }
        except GuardrailTrippedError as exc:
            status = AgentRunStatus.FAILED
            stop_reason = f"guardrail:{exc.reason}"
            final_text = "I stopped this run for safety: " + exc.reason
            yield {"type": "error", "detail": exc.reason}
        except AgentProviderError as exc:
            status = AgentRunStatus.FAILED
            stop_reason = "provider_error"
            final_text = "The model provider is unavailable right now. Please try again."
            logger.warning("agent provider error: %s", exc)
            yield {"type": "error", "detail": "provider_error"}

        await convos.add_message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            channel=message_channel,
            content=final_text,
            agent_run_id=run.id,
        )
        await runs.finish(
            run,
            status=status,
            stop_reason=stop_reason,
            input_tokens=total_in,
            output_tokens=total_out,
            latency_ms=latency,
            step_count=steps,
        )
        await self._s.commit()

        yield {"type": "assistant_message", "content": final_text}
        yield {"type": "run_completed", "run_id": str(run.id), "status": status.value}

    async def run_result(
        self,
        *,
        conversation: Conversation,
        project: Project,
        user: User,
        user_message: str,
        message_channel: MessageChannel = MessageChannel.VOICE,
    ) -> dict[str, Any]:
        """Collect the canonical event stream into one channel-independent result.

        The work itself stays in ``run`` so voice obeys exactly the same context,
        policies, approvals, audit trail, and tools as text chat.
        """
        display: list[dict[str, Any]] = []
        approval: dict[str, Any] | None = None
        speech_text = ""
        run_id: str | None = None
        final_status = "failed"
        async for event in self.run(
            conversation=conversation,
            project=project,
            user=user,
            user_message=user_message,
            message_channel=message_channel,
        ):
            event_type = event.get("type")
            if event_type == "run_started":
                run_id = event.get("run_id")
            elif event_type == "assistant_message":
                speech_text = str(event.get("content") or "")
            elif event_type == "approval_required":
                tool_name = event.get("tool")
                approval = {
                    "required": True,
                    "detail": event.get("detail", ""),
                    "action_id": event.get("action_id"),
                    "tool": tool_name,
                    "confirmable": bool(
                        event.get("action_id") and tool_name in INLINE_CONFIRMABLE_TOOLS
                    ),
                }
            elif event_type in {"tool_call", "tool_result", "error"}:
                # Deliberately compact: tool argument payloads can include
                # sensitive values and are not useful in spoken output.
                display.append(
                    {
                        "type": event_type,
                        "tool": event.get("tool"),
                        "summary": event.get("summary") or event.get("detail"),
                        "ok": event.get("ok"),
                    }
                )
            elif event_type == "run_completed":
                run_id = event.get("run_id") or run_id
                final_status = str(event.get("status") or "failed")

        outcome = {
            AgentRunStatus.SUCCEEDED.value: "completed",
            AgentRunStatus.AWAITING_APPROVAL.value: "pending",
        }.get(final_status, "failed")
        return {
            "status": outcome,
            "speech_text": speech_text,
            "display": display,
            "approval": approval,
            "agent_run_id": run_id,
        }

    # -- internals -----------------------------------------------------

    async def _run_tool(
        self,
        call: Any,
        ctx: ToolContext,
        audit: AuditLogger,
        repeated: RepeatedCallGuard,
        messages: list[Message],
    ) -> AsyncIterator[StreamEvent]:
        repeated.record(call.name, call.arguments)
        scan_for_secrets(call.arguments, where=f"tool args for {call.name}")
        yield {"type": "tool_call", "tool": call.name, "args": call.arguments}

        def _fail(payload: dict[str, Any], summary: str) -> StreamEvent:
            messages.append(
                Message(role="tool", tool_call_id=call.id, content=json.dumps(payload, default=str))
            )
            return {"type": "tool_result", "tool": call.name, "ok": False, "summary": summary}

        tool = self._registry.get(call.name)
        started = time.perf_counter()
        if tool is None:
            yield _fail({"error": f"unknown tool: {call.name}"}, f"unknown tool: {call.name}")
            return

        try:
            args = tool.Args.model_validate(call.arguments)
        except ValidationError as exc:
            yield _fail(
                {"error": "invalid arguments", "details": exc.errors(include_url=False)},
                "invalid arguments",
            )
            return

        policy = self._policy.check(tool=tool.name, risk=tool.risk)
        decision = self._permission.decide(risk=tool.risk, policy=policy)
        if not decision.allowed:
            yield _fail({"error": decision.reason or "Denied by project policy"}, "policy denied")
            return
        if decision.requires_approval:
            action_id = await audit.record_pending_approval(
                run_id=ctx.run_id,
                project_id=ctx.project.id,
                tool=tool.name,
                risk=tool.risk,
                args=args.model_dump(mode="json"),
                policy=decision,
            )
            raise ApprovalRequiredError(
                f"{tool.name} ({tool.risk.value}) needs human approval: {decision.reason}.",
                action_id=action_id,
                tool=tool.name,
            )

        result = await tool.run(args, ctx)
        scan_for_secrets(result.model_dump(), where=f"tool result for {call.name}")
        duration_ms = int((time.perf_counter() - started) * 1000)

        await audit.record_tool_call(
            run_id=ctx.run_id,
            project_id=ctx.project.id,
            tool=tool.name,
            risk=tool.risk,
            args=call.arguments,
            status=AgentActionStatus.OK if result.ok else AgentActionStatus.ERROR,
            summary={"summary": result.summary},
            policy=decision,
            duration_ms=duration_ms,
        )

        result_payload: dict[str, Any] = (
            (result.data or {}) if result.ok else {"error": result.error or "failed"}
        )
        messages.append(
            Message(
                role="tool",
                tool_call_id=call.id,
                content=json.dumps(result_payload, default=str),
            )
        )
        yield {
            "type": "tool_result",
            "tool": call.name,
            "ok": result.ok,
            "summary": result.summary,
        }
