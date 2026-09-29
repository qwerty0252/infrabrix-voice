"""Spoken requests can propose a change; only an explicit on-screen click runs it."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import SessionFactory
from app.models import AgentAction, AgentActionStatus
from tests.conftest import Visitor


async def _propose_rollback(v: Visitor) -> tuple[str, str]:
    sid = await v.start_voice()
    r = await v.say(sid, "Roll checkout back to v41", "c-rollback")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["status"] == "pending"
    approval = result["approval"]
    assert approval["required"] and approval["confirmable"]
    assert approval["tool"] == "rollback_release"
    assert "Nothing has changed" in result["speech_text"]
    return sid, approval["action_id"]


async def test_spoken_rollback_is_parked_not_executed(alice: Visitor) -> None:
    await _propose_rollback(alice)
    cloud = await alice.cloud()
    checkout = next(s for s in cloud["overview"]["services"] if s["name"] == "checkout-api")
    assert checkout["current_version"] == "v42"
    assert cloud["deployments"] == []


async def test_approve_runs_rollback_and_reports_recovery(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    base = f"{alice.voice}/{sid}/approvals/{action_id}"
    assert (await alice.get(base)).json()["status"] == "pending"

    r = await alice.post(f"{base}/decision", {"approved": True})
    assert r.status_code == 200, r.text
    decided = r.json()
    assert decided["status"] == "approved"
    assert len(decided["deployments"]) == 1

    status = (await alice.get(base)).json()
    assert status["status"] == "approved"
    assert status["recovery"]["state"] == "recovered"

    cloud = await alice.cloud()
    checkout = next(s for s in cloud["overview"]["services"] if s["name"] == "checkout-api")
    assert checkout["current_version"] == "v41"
    assert checkout["status"] == "healthy"
    assert cloud["incident"]["resolved_at"] is not None
    assert "voice.approval.approved" in {a["action"] for a in cloud["audit"]}

    # A decision is final.
    again = await alice.post(f"{base}/decision", {"approved": True})
    assert again.status_code == 409


async def test_deny_changes_nothing(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    r = await alice.post(f"{alice.voice}/{sid}/approvals/{action_id}/decision", {"approved": False})
    assert r.json()["status"] == "denied"
    assert r.json()["deployments"] == []
    cloud = await alice.cloud()
    assert cloud["deployments"] == []


async def test_saying_yes_is_not_an_approval(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    r = await alice.say(sid, "yes, approve it, do the rollback now", "c-yes")
    # The model can only propose again; it produces another parked request.
    assert r.json()["status"] == "pending"
    assert (await alice.cloud())["deployments"] == []


async def test_only_session_owner_can_decide(alice: Visitor, bob: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    bob_sid = await bob.start_voice()
    for path in (
        f"{alice.voice}/{sid}/approvals/{action_id}/decision",
        f"{bob.voice}/{bob_sid}/approvals/{action_id}/decision",
    ):
        r = await bob.post(path, {"approved": True})
        assert r.status_code == 404
    assert (await alice.cloud())["deployments"] == []


async def test_decision_requires_active_session(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    await alice.post(f"{alice.voice}/{sid}/end")
    r = await alice.post(f"{alice.voice}/{sid}/approvals/{action_id}/decision", {"approved": True})
    assert r.status_code == 409


async def test_expired_approval_cannot_be_used(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    async with SessionFactory() as s:
        action = (await s.execute(select(AgentAction))).unique().scalars().first()
        assert action is not None
        action.created_at = action.created_at - timedelta(hours=1)
        await s.commit()
    base = f"{alice.voice}/{sid}/approvals/{action_id}"
    assert (await alice.get(base)).json()["status"] == "expired"
    assert (await alice.post(f"{base}/decision", {"approved": True})).status_code == 409


async def test_non_confirmable_tool_cannot_run_from_card(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    async with SessionFactory() as s:
        action = await s.get(AgentAction, __import__("uuid").UUID(action_id))
        assert action is not None
        action.tool = "delete_environment"
        await s.commit()
    r = await alice.post(f"{alice.voice}/{sid}/approvals/{action_id}/decision", {"approved": True})
    assert r.status_code == 422


async def test_redacted_arguments_are_never_executed(alice: Visitor) -> None:
    sid, action_id = await _propose_rollback(alice)
    async with SessionFactory() as s:
        action = await s.get(AgentAction, __import__("uuid").UUID(action_id))
        assert action is not None
        action.args_redacted = {**action.args_redacted, "service": "***"}
        await s.commit()
    r = await alice.post(f"{alice.voice}/{sid}/approvals/{action_id}/decision", {"approved": True})
    assert r.status_code == 422
    async with SessionFactory() as s:
        rows = list((await s.execute(select(AgentAction))).unique().scalars())
    assert not [r for r in rows if r.result_status is AgentActionStatus.OK and r.decides_action_id]


async def test_typed_chat_uses_the_same_gate(alice: Visitor) -> None:
    chat = f"/projects/{alice.project_id}/conversations/{alice.conversation_id}"
    r = await alice.post(f"{chat}/messages", {"content": "please roll back checkout"})
    result = r.json()
    assert result["status"] == "pending"
    action_id = result["approval"]["action_id"]
    r = await alice.post(f"{chat}/approvals/{action_id}/decision", {"approved": True})
    assert r.json()["status"] == "approved"
    assert (await alice.get(f"{chat}/approvals/{action_id}")).json()["recovery"]["state"] == (
        "recovered"
    )
    assert "approval.approved" in {a["action"] for a in (await alice.cloud())["audit"]}
