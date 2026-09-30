"""The simulated cloud and the offline planner."""

from __future__ import annotations

import pytest

from app import democloud
from app.providers.llm.scripted import plan_tool_call
from tests.conftest import Visitor


def test_diagnosis_blames_the_release_before_the_spike() -> None:
    result = democloud.diagnose(democloud.seed_state())
    assert result["service"] == "ria-api"
    assert result["suspect_release"]["version"] == "v24"
    assert result["recommendation"]["to_version"] == "v23"


def test_no_incident_once_resolved() -> None:
    state = democloud.seed_state()
    state["services"]["ria-api"]["current_version"] = "v23"
    assert democloud.diagnose(state)["headline"] == "I don't see an active incident."


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("what's wrong with production", "diagnose_incident"),
        ("how much are we spending this month", "get_cost_summary"),
        ("roll the api back to version 22", "rollback_release"),
        ("show me the api logs", "get_recent_errors"),
        ("what's the latency on the web app", "get_service_metrics"),
        ("what did we deploy today", "list_releases"),
        ("give me a status update", "get_project_overview"),
    ],
)
def test_planner_intents(text: str, tool: str) -> None:
    planned = plan_tool_call(text)
    assert planned is not None and planned[0] == tool


def test_planner_extracts_rollback_target() -> None:
    assert plan_tool_call("roll the api back to version 22") == (
        "rollback_release",
        {"service": "ria-api", "to_version": "v22"},
    )


def test_greeting_needs_no_tool() -> None:
    assert plan_tool_call("hey there") is None


async def test_reset_restores_the_incident(alice: Visitor) -> None:
    chat = f"/projects/{alice.project_id}/conversations/{alice.conversation_id}"
    action = (await alice.post(f"{chat}/messages", {"content": "roll back the api"})).json()
    await alice.post(
        f"{chat}/approvals/{action['approval']['action_id']}/decision", {"approved": True}
    )
    assert (await alice.cloud())["overview"]["degraded"] == []
    await alice.post(f"/projects/{alice.project_id}/cloud/reset")
    assert (await alice.cloud())["overview"]["degraded"] == ["ria-api"]
