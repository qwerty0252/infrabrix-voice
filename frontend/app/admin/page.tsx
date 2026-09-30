"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

type Health = { llm_provider: string; voice_configured: boolean };

/** Presenter controls for this browser's demo environment. Not linked from the main page. */
export default function AdminPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.me().then(setMe).catch(() => setStatus("Open the demo page first to create your environment."));
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  const reset = async () => {
    if (!me) return;
    setBusy(true);
    try {
      await api.resetCloud(me.project_id);
      setStatus("Reset. ria-api is failing again on v24, ready for the next take.");
    } catch {
      setStatus("Reset failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-4 px-4 py-8">
      <Link href="/" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to the demo
      </Link>
      <h1 className="text-xl font-semibold">Demo admin</h1>
      <section className="rounded-2xl border border-border bg-card/40 p-4">
        <h2 className="text-sm font-medium">Environment</h2>
        <div className="mt-2 flex flex-wrap gap-2">
          {health ? (
            <>
              <Badge tone={health.voice_configured ? "success" : "warning"}>
                voice {health.voice_configured ? "configured" : "key not set"}
              </Badge>
              <Badge>planner: {health.llm_provider}</Badge>
            </>
          ) : (
            <Badge>checking…</Badge>
          )}
        </div>
      </section>
      <section className="rounded-2xl border border-border bg-card/40 p-4">
        <h2 className="text-sm font-medium">Reset incident</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Puts this browser&apos;s RIA environment back to the failing v24 release so the demo can be replayed. The
          conversation history and audit trail are kept.
        </p>
        <Button className="mt-3" disabled={!me || busy} onClick={() => void reset()}>
          <RotateCcw className="h-4 w-4" /> Reset demo
        </Button>
        {status && <p className="mt-3 text-sm text-muted-foreground">{status}</p>}
      </section>
    </main>
  );
}
