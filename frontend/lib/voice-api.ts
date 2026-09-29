"use client";

import { request } from "@/lib/api";
import type { ApprovalDecisionResult, ApprovalState, VoiceSessionStart } from "@/lib/types";

export type VoiceClientEventType = "reconnect_attempt" | "disconnected" | "error" | "approval_shown";

const sessionPath = (pid: string, sid: string) => `/projects/${pid}/voice-sessions/${sid}`;

export const voiceApi = {
  /** Fresh provider token for a dropped session; the old session is marked disconnected. */
  reconnect: (pid: string, sid: string) =>
    request<VoiceSessionStart>(`${sessionPath(pid, sid)}/reconnect`, { method: "POST" }),
  approvalStatus: (pid: string, sid: string, actionId: string) =>
    request<ApprovalState>(`${sessionPath(pid, sid)}/approvals/${actionId}`),
  decideApproval: (pid: string, sid: string, actionId: string, approved: boolean) =>
    request<ApprovalDecisionResult>(`${sessionPath(pid, sid)}/approvals/${actionId}/decision`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  /** Best-effort connection telemetry: codes only, never transcripts. */
  reportEvent: (pid: string, sid: string, type: VoiceClientEventType, code?: string, attempt?: number) =>
    request<void>(`${sessionPath(pid, sid)}/events`, {
      method: "POST",
      body: JSON.stringify({ type, code, attempt }),
    }).catch(() => undefined),
};
