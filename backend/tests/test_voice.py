"""Voice session lifecycle, the provider bridge, and tenant isolation."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionFactory
from app.models import AgentRun, AuditEvent
from app.providers.voice import get_voice_provider
from app.providers.voice.assemblyai import AssemblyAIVoiceProvider
from app.settings import Settings
from tests.conftest import Visitor


async def test_session_returns_temporary_token_and_single_bridge_tool(alice: Visitor) -> None:
    r = await alice.post(alice.voice, {"conversation_id": alice.conversation_id})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] == "temp-token-1"
    assert "server-only-test-key" not in r.text
    tools = body["session_config"]["tools"]
    assert [t["name"] for t in tools] == ["brix_execute_turn"]
    assert body["session"]["status"] == "created"


async def test_unconfigured_provider_fails_closed(alice: Visitor) -> None:
    from app.main import app

    app.dependency_overrides[get_voice_provider] = lambda: AssemblyAIVoiceProvider(
        Settings(ASSEMBLYAI_API_KEY=None)
    )
    r = await alice.post(alice.voice, {"conversation_id": alice.conversation_id})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "voice_not_configured"


async def test_requires_authentication(alice: Visitor) -> None:
    r = await alice.client.post(alice.voice, json={"conversation_id": alice.conversation_id})
    assert r.status_code == 401
    forged = {"Authorization": f"Bearer {alice.token[:-4]}AAAA"}
    r = await alice.client.post(
        alice.voice, json={"conversation_id": alice.conversation_id}, headers=forged
    )
    assert r.status_code == 401


async def test_turn_requires_connected_session(alice: Visitor) -> None:
    sid = await alice.start_voice(connect=False)
    r = await alice.say(sid, "how is production?", "c1")
    assert r.status_code == 409


async def test_read_turn_runs_canonical_agent(alice: Visitor) -> None:
    sid = await alice.start_voice()
    r = await alice.say(sid, "What's wrong with the API?", "c1")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["status"] == "completed"
    assert "v24" in result["speech_text"]
    assert result["approval"] is None
    assert [e["tool"] for e in result["display"]] == ["diagnose_incident", "diagnose_incident"]

    messages = (
        await alice.get(
            f"/projects/{alice.project_id}/conversations/{alice.conversation_id}/messages"
        )
    ).json()
    assert [(m["role"], m["channel"]) for m in messages] == [
        ("user", "voice"),
        ("assistant", "voice"),
    ]


async def test_turn_is_idempotent_per_provider_call(alice: Visitor) -> None:
    sid = await alice.start_voice()
    first = (await alice.say(sid, "how much am I spending?", "same")).json()
    second = (await alice.say(sid, "something else entirely", "same")).json()
    assert first == second
    async with SessionFactory() as s:
        assert (await s.execute(select(func.count()).select_from(AgentRun))).scalar_one() == 1


async def test_forged_bridge_fields_are_rejected(alice: Visitor) -> None:
    sid = await alice.start_voice()
    r = await alice.post(
        f"{alice.voice}/{sid}/turns",
        {"call_id": "c1", "message": "roll back", "project_id": "someone-else", "approved": True},
    )
    assert r.status_code == 422


async def test_other_users_cannot_touch_a_session(alice: Visitor, bob: Visitor) -> None:
    sid = await alice.start_voice()
    # Bob using Alice's project: the project itself is invisible to him.
    r = await bob.post(f"{alice.voice}/{sid}/turns", {"call_id": "x", "message": "roll back"})
    assert r.status_code == 404
    # Bob using his own project with Alice's session ID.
    r = await bob.post(f"{bob.voice}/{sid}/turns", {"call_id": "x", "message": "roll back"})
    assert r.status_code == 404
    r = await bob.post(f"{bob.voice}/{sid}/end")
    assert r.status_code == 404
    # And he cannot start a session on Alice's conversation.
    r = await bob.post(bob.voice, {"conversation_id": alice.conversation_id})
    assert r.status_code == 404


async def test_ended_session_cannot_run_or_reconnect(alice: Visitor) -> None:
    sid = await alice.start_voice()
    assert (await alice.post(f"{alice.voice}/{sid}/end")).status_code == 200
    assert (await alice.say(sid, "status?", "c1")).status_code == 409
    assert (await alice.post(f"{alice.voice}/{sid}/reconnect")).status_code == 409


async def test_reconnect_issues_new_session_on_same_conversation(alice: Visitor) -> None:
    sid = await alice.start_voice()
    r = await alice.post(f"{alice.voice}/{sid}/reconnect")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["id"] != sid
    assert body["session"]["conversation_id"] == alice.conversation_id
    assert body["token"] == "temp-token-2"
    # The dropped session can no longer run turns.
    assert (await alice.say(sid, "status?", "c1")).status_code == 409


async def test_client_telemetry_accepts_codes_only(alice: Visitor) -> None:
    sid = await alice.start_voice()
    events = f"{alice.voice}/{sid}/events"
    ok = await alice.post(
        events, {"type": "reconnect_attempt", "code": "ws_close_1006", "attempt": 1}
    )
    assert ok.status_code == 204
    free_text = await alice.post(events, {"type": "error", "code": "user said my password is x"})
    assert free_text.status_code == 422
    extra = await alice.post(events, {"type": "error", "transcript": "hello"})
    assert extra.status_code == 422


async def test_audit_never_stores_transcripts(alice: Visitor) -> None:
    sid = await alice.start_voice()
    secretish = "tell me about the zebra-launch-codes please"
    await alice.say(sid, secretish, "c1")
    async with SessionFactory() as s:
        rows = list((await s.execute(select(AuditEvent))).scalars())
    actions = {r.action for r in rows}
    assert {"voice.session.started", "voice.session.connected", "voice.turn"} <= actions
    assert all("zebra" not in str(r.payload) for r in rows)
