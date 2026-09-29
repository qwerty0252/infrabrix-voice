"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Keyboard, Mic, Send, ShieldCheck, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type PendingApproval = {
  actionId: string;
  tool: string;
  phase: "awaiting" | "deciding" | "recovering" | "done";
  outcome?: string;
};

const SUGGESTIONS = [
  "What's wrong with production?",
  "Roll checkout back to the last good release",
  "How much are we spending this month?",
];

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * The conversation both channels share, plus a typed fallback for people
 * without a microphone. Typed turns go through the same runtime and gate.
 */
export function ChatPanel({
  projectId,
  conversationId,
  refreshKey,
  onTurn,
}: {
  projectId: string;
  conversationId: string;
  refreshKey: number;
  onTurn: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .messages(projectId, conversationId)
      .then((rows) => !cancelled && setMessages(rows))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId, conversationId, refreshKey]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    setBusy(true);
    setError(null);
    setDraft("");
    try {
      const result = await api.sendMessage(projectId, conversationId, content);
      if (result.approval?.confirmable && result.approval.action_id) {
        setApproval({ actionId: result.approval.action_id, tool: result.approval.tool ?? "change", phase: "awaiting" });
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Brix could not complete that request.");
    } finally {
      setBusy(false);
      onTurn();
    }
  };

  const decide = async (approved: boolean) => {
    if (!approval || approval.phase !== "awaiting") return;
    const { actionId } = approval;
    setApproval({ ...approval, phase: "deciding" });
    try {
      const result = await api.decideChatApproval(projectId, conversationId, actionId, approved);
      onTurn();
      if (result.status !== "approved" || result.deployments.length === 0) {
        setApproval((a) => (a ? { ...a, phase: "done", outcome: result.speech_text } : a));
        return;
      }
      setApproval((a) => (a ? { ...a, phase: "recovering" } : a));
      for (let i = 0; i < 60; i += 1) {
        await wait(2000);
        const state = await api.chatApprovalStatus(projectId, conversationId, actionId).catch(() => null);
        onTurn();
        const recovery = state?.recovery?.state;
        if (recovery && recovery !== "in_progress") {
          const text =
            recovery === "recovered" ? "Recovered. The rollback deployed successfully." : "The rollback did not finish cleanly.";
          setApproval((a) => (a ? { ...a, phase: "done", outcome: text } : a));
          return;
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not record your decision.");
      setApproval((a) => (a ? { ...a, phase: "awaiting" } : a));
    }
  };

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border border-border bg-card/40">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-medium">Conversation</h2>
        <span className="text-xs text-muted-foreground">Voice and typed turns share one history</span>
      </header>
      <div ref={listRef} className="flex max-h-80 min-h-40 flex-col gap-2 overflow-y-auto px-4 py-3">
        {messages.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Start Voice Mode and ask what&apos;s wrong, or type below.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={cn("flex gap-2", m.role === "user" && "justify-end")}>
            <div
              className={cn(
                "max-w-[85%] rounded-xl px-3 py-2 text-sm",
                m.role === "user" ? "bg-accent/15" : "bg-muted/70",
              )}
            >
              <p className="mb-0.5 flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                {m.channel === "voice" ? <Mic className="h-3 w-3" /> : <Keyboard className="h-3 w-3" />}
                {m.role === "user" ? "You" : "Brix"}
              </p>
              {m.content}
            </div>
          </div>
        ))}
      </div>
      {approval && (
        <div role="alertdialog" aria-label="Approval required" className="mx-4 mb-3 rounded-xl border border-warning/50 bg-warning/5 p-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck className="h-4 w-4" /> Approve {approval.tool.replaceAll("_", " ")}?
          </p>
          {approval.phase === "awaiting" || approval.phase === "deciding" ? (
            <div className="mt-2 flex gap-2">
              <Button size="sm" disabled={approval.phase === "deciding"} onClick={() => void decide(true)}>
                <Check className="h-3.5 w-3.5" /> Approve
              </Button>
              <Button size="sm" variant="outline" disabled={approval.phase === "deciding"} onClick={() => void decide(false)}>
                <X className="h-3.5 w-3.5" /> Deny
              </Button>
            </div>
          ) : (
            <p className="mt-1 text-xs">{approval.phase === "recovering" ? "Watching the rollback deployment…" : approval.outcome}</p>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2 px-4 pb-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy}
            onClick={() => void send(s)}
            className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>
      <form
        className="flex gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="No microphone? Type to Brix…"
          className="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Button type="submit" disabled={busy || !draft.trim()}>
          <Send className="h-4 w-4" />
        </Button>
      </form>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </section>
  );
}
