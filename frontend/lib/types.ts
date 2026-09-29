export type VoiceSession = {
  id: string;
  status: string;
  conversation_id: string;
  correlation_id: string;
};

export type VoiceSessionStart = {
  session: VoiceSession;
  token: string;
  websocket_url: string;
  expires_in_seconds: number;
  max_session_duration_seconds: number;
  session_config: Record<string, unknown>;
};

export type TurnApproval = {
  required: true;
  detail: string;
  action_id?: string | null;
  tool?: string | null;
  confirmable?: boolean;
};

export type TurnResult = {
  status: "completed" | "pending" | "failed";
  speech_text: string;
  display: { type: string; tool?: string | null; summary?: string | null; ok?: boolean | null }[];
  approval: TurnApproval | null;
  agent_run_id: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  channel: "text" | "voice";
  content: string;
  created_at: string;
};

export type Service = {
  name: string;
  kind: string;
  current_version: string;
  status: "healthy" | "degraded" | "rolling_back";
  error_rate_percent?: number;
  p95_latency_ms?: number;
  requests_per_min?: number;
  cpu_percent?: number;
  connections?: number;
};

export type Release = {
  version: string;
  deployed_at: string;
  commit: string;
  message: string;
  author: string;
  live: boolean;
};

export type CloudState = {
  project: { id: string; name: string; environment: string };
  overview: { region: string; services: Service[]; degraded: string[]; headline: string };
  releases: Record<string, Release[]>;
  incident: { service: string; version: string; started_at: string; resolved_at: string | null } | null;
  deployments: {
    id: string;
    service: string;
    from_version: string;
    to_version: string;
    status: "in_progress" | "succeeded" | "failed";
    created_at: string;
  }[];
  audit: { action: string; actor: string; at: string }[];
};

export type ApprovalState = {
  action_id: string;
  tool: string;
  confirmable: boolean;
  status: "pending" | "approved" | "denied" | "failed" | "expired";
  recovery?: { state: "in_progress" | "recovered" | "failed"; deployments: { id: string; status: string }[] };
};

export type ApprovalDecisionResult = {
  status: "approved" | "denied" | "failed";
  ok: boolean;
  action_id: string;
  tool: string;
  speech_text: string;
  deployments: string[];
};

export type Me = {
  user: { id: string; display_name: string };
  project_id: string;
  conversation_id: string | null;
};
