# Voice architecture

Voice Mode is an additional interface to the existing agent. It is not a new agent, a parallel
tool registry, or a second source of truth for conversation state.

## Responsibilities

AssemblyAI owns speech in, speech out, browser transport, turn detection and interruption.
InfraBrix owns user and project identity, conversation state, reasoning, tool choice, policy,
approvals and audit. The only thing that crosses the boundary is one high-level tool,
`brix_execute_turn(message)`, and its result.

## A read turn

```mermaid
sequenceDiagram
  participant User
  participant Browser
  participant AAI as AssemblyAI
  participant Gateway as Voice gateway
  participant Brix as AgentRuntime
  User->>Browser: "What's wrong with production?"
  Browser->>AAI: PCM16 audio over WebSocket
  AAI->>Browser: tool.call brix_execute_turn(message)
  Browser->>Gateway: POST /voice-sessions/{id}/turns (bearer, call_id)
  Gateway->>Gateway: owned + active session? idempotent call_id?
  Gateway->>Brix: canonical turn (user, project, conversation from session)
  Brix->>Brix: diagnose_incident (READ, runs automatically)
  Brix-->>Gateway: speech_text, display events, approval=null
  Gateway-->>Browser: result
  Browser->>AAI: tool.result
  AAI-->>User: spoken answer
```

## A change

```mermaid
sequenceDiagram
  participant User
  participant Browser
  participant AAI as AssemblyAI
  participant Gateway as Voice gateway
  participant Brix as AgentRuntime
  User->>AAI: "Roll it back"
  AAI->>Browser: tool.call brix_execute_turn
  Browser->>Gateway: turn
  Gateway->>Brix: canonical turn
  Brix->>Brix: rollback_release is WRITE_INFRASTRUCTURE → park as pending_approval
  Brix-->>Browser: status=pending, approval{action_id, confirmable}
  AAI-->>User: "Nothing has changed yet. Approve it on screen."
  User->>Browser: clicks Approve
  Browser->>Gateway: POST /approvals/{action}/decision (separate endpoint)
  Gateway->>Brix: re-validate args, run tool, record decision (unique per action)
  Browser->>Gateway: poll approval status → recovery: in_progress → recovered
```

The decision endpoint is not part of the provider bridge. Nothing AssemblyAI or the model produces
can call it. It requires the signed-in user, their own active voice session, an unexpired and
undecided parked action in that session's conversation, and a tool on the voice allow-list.

## Reconnection

Provider tokens are short-lived and single-use. On an unexpected socket close, the browser calls
`/reconnect`, which marks the old session `disconnected` and issues a new session and token on the
same conversation. A session the user ended cannot be resumed. The mic and audio graph stay open
across retries (3 attempts, 1s/2s/4s).

## Telemetry

Lifecycle events are audit entries: `voice.session.started|connected|reconnected|ended`,
`voice.turn`, `voice.approval.approved|denied|failed`, and client-reported `voice.client.*`. Client
events accept a fixed type and a code matching `^[a-z0-9_.-]+$`. Transcripts are stored in the
conversation, never in telemetry.

## Simulated cloud

`backend/app/democloud.py` gives each visitor a private, stateful environment: four services, a
release history, an incident introduced by the latest `ria-api` release, matching metrics and logs,
a cost summary, and a rollback that completes after `INFRABRIX_DEMO_ROLLBACK_SECONDS`. The tools in
`agent/tools.py` are the only code that touches it. In the InfraBrix product, the same tool
interface is backed by real cloud providers.
