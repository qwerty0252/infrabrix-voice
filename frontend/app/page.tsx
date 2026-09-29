"use client";

import { useCallback, useEffect, useState } from "react";
import { AudioLines } from "lucide-react";
import { api, ApiError, setToken } from "@/lib/api";
import type { CloudState, Me } from "@/lib/types";
import { VoiceMode } from "@/components/voice-mode";
import { CloudPanel } from "@/components/cloud-panel";
import { ChatPanel } from "@/components/chat-panel";
import { Badge } from "@/components/ui/badge";

const CLOUD_POLL_MS = 3000;

async function ensureSignedIn(): Promise<Me> {
  try {
    return await api.me();
  } catch (e) {
    if (!(e instanceof ApiError) || (e.status !== 401 && e.status !== 404)) throw e;
    const created = await api.signIn("Demo engineer");
    setToken(created.token);
    return created;
  }
}

export default function Home() {
  const [me, setMe] = useState<Me | null>(null);
  const [cloud, setCloud] = useState<CloudState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [health, setHealth] = useState<{ llm_provider: string; voice_configured: boolean } | null>(null);

  useEffect(() => {
    ensureSignedIn()
      .then(setMe)
      .catch(() => setError("Could not reach the InfraBrix Voice backend. Is it running on port 8000?"));
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  const refresh = useCallback(() => {
    if (!me) return;
    api.cloud(me.project_id).then(setCloud).catch(() => undefined);
    setRefreshKey((k) => k + 1);
  }, [me]);

  useEffect(() => {
    if (!me) return;
    refresh();
    const timer = setInterval(() => {
      api.cloud(me.project_id).then(setCloud).catch(() => undefined);
    }, CLOUD_POLL_MS);
    return () => clearInterval(timer);
  }, [me, refresh]);

  const reset = async () => {
    if (!me) return;
    await api.resetCloud(me.project_id);
    refresh();
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent/15 text-accent">
            <AudioLines className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight">InfraBrix Voice</h1>
            <p className="text-xs text-muted-foreground">Talk to your on-call DevOps agent</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="accent">AssemblyAI Voice Agent API</Badge>
          {health && <Badge>planner: {health.llm_provider}</Badge>}
          {health && !health.voice_configured && <Badge tone="warning">voice key not set</Badge>}
        </div>
      </header>

      {error && <p className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}

      <p className="max-w-3xl text-sm text-muted-foreground">
        Your checkout service started failing after the last deploy. Press <em>Start Voice Mode</em> and ask Brix
        what&apos;s wrong. It investigates with typed tools, proposes a fix, and waits for you to approve it on
        screen. Saying &ldquo;yes&rdquo; out loud never changes anything.
      </p>

      {me?.conversation_id ? (
        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-12">
          <div className="flex min-h-[560px] flex-col gap-4 lg:col-span-7">
            <VoiceMode projectId={me.project_id} conversationId={me.conversation_id} onTurn={refresh} />
            <ChatPanel
              projectId={me.project_id}
              conversationId={me.conversation_id}
              refreshKey={refreshKey}
              onTurn={refresh}
            />
          </div>
          <div className="lg:col-span-5">
            <CloudPanel cloud={cloud} onReset={() => void reset()} />
          </div>
        </div>
      ) : (
        !error && <p className="text-sm text-muted-foreground">Preparing your demo environment…</p>
      )}

      <footer className="pt-4 text-center text-xs text-muted-foreground">
        The voice layer of{" "}
        <a href="https://infrabrix.enylabs.com/" className="underline hover:text-foreground">
          InfraBrix
        </a>
        , running against a simulated cloud. Apache-2.0.
      </footer>
    </main>
  );
}
