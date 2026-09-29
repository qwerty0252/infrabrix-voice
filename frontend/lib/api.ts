"use client";

import type {
  ApprovalDecisionResult,
  ApprovalState,
  ChatMessage,
  CloudState,
  Me,
  TurnResult,
  VoiceSession,
  VoiceSessionStart,
} from "@/lib/types";

const BASE = "/api/backend";
const TOKEN_KEY = "infrabrix-voice-token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the session simply won't persist */
  }
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(message: string, code: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    let code = "error";
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code ?? code;
    } catch {
      /* ignore */
    }
    throw new ApiError(message, code, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

const chatPath = (pid: string, cid: string) => `/projects/${pid}/conversations/${cid}`;

export const api = {
  signIn: (display_name: string) =>
    request<Me & { token: string }>("/auth/demo", post({ display_name })),
  me: () => request<Me>("/me"),
  health: () => request<{ ok: boolean; llm_provider: string; voice_configured: boolean }>("/health"),
  cloud: (pid: string) => request<CloudState>(`/projects/${pid}/cloud`),
  resetCloud: (pid: string) => request<{ ok: boolean }>(`/projects/${pid}/cloud/reset`, post()),

  messages: (pid: string, cid: string) => request<ChatMessage[]>(`${chatPath(pid, cid)}/messages`),
  sendMessage: (pid: string, cid: string, content: string) =>
    request<TurnResult>(`${chatPath(pid, cid)}/messages`, post({ content })),
  chatApprovalStatus: (pid: string, cid: string, actionId: string) =>
    request<ApprovalState>(`${chatPath(pid, cid)}/approvals/${actionId}`),
  decideChatApproval: (pid: string, cid: string, actionId: string, approved: boolean) =>
    request<ApprovalDecisionResult>(`${chatPath(pid, cid)}/approvals/${actionId}/decision`, post({ approved })),

  createVoiceSession: (pid: string, body: { conversation_id: string }) =>
    request<VoiceSessionStart>(`/projects/${pid}/voice-sessions`, post(body)),
  connectVoiceSession: (pid: string, sessionId: string, provider_session_id: string) =>
    request<{ session: VoiceSession }>(
      `/projects/${pid}/voice-sessions/${sessionId}/connected`,
      post({ provider_session_id }),
    ),
  executeVoiceTurn: (pid: string, sessionId: string, call_id: string, message: string) =>
    request<TurnResult>(`/projects/${pid}/voice-sessions/${sessionId}/turns`, post({ call_id, message })),
  endVoiceSession: (pid: string, sessionId: string) =>
    request<{ session: VoiceSession }>(`/projects/${pid}/voice-sessions/${sessionId}/end`, post()),
};
