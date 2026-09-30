"use client";

import { Activity, GitCommit, ScrollText } from "lucide-react";
import type { CloudState, Service } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS_TONE: Record<Service["status"], "success" | "destructive" | "warning"> = {
  healthy: "success",
  degraded: "destructive",
  rolling_back: "warning",
};

function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

export function CloudPanel({ cloud }: { cloud: CloudState | null }) {
  if (!cloud) {
    return <Panel title="Production" icon={<Activity className="h-4 w-4" />}>Loading…</Panel>;
  }
  const releaseService = cloud.incident?.service ?? Object.keys(cloud.releases)[0] ?? "";
  const releases = cloud.releases[releaseService] ?? [];
  return (
    <div className="flex flex-col gap-4">
      <Panel
        title={`${cloud.project.name} · ${cloud.project.environment}`}
        icon={<Activity className="h-4 w-4" />}
      >
        <p className="mb-3 text-xs text-muted-foreground">
          Simulated cloud · {cloud.overview.region}. {cloud.overview.headline}
        </p>
        <ul className="flex flex-col gap-2">
          {cloud.overview.services.map((s) => (
            <li key={s.name} className="rounded-lg border border-border bg-background/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "h-2 w-2 rounded-full",
                      s.status === "healthy" && "bg-success",
                      s.status === "degraded" && "animate-pulse bg-destructive",
                      s.status === "rolling_back" && "animate-pulse bg-warning",
                    )}
                  />
                  <span className="font-mono text-sm">{s.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">{s.current_version}</span>
                </div>
                <Badge tone={STATUS_TONE[s.status]}>{s.status.replace("_", " ")}</Badge>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{s.kind}</p>
              {s.error_rate_percent !== undefined && (
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                  <Metric label="errors" value={`${s.error_rate_percent}%`} bad={s.status !== "healthy" && s.error_rate_percent > 5} />
                  <Metric label="p95" value={`${s.p95_latency_ms} ms`} bad={s.status !== "healthy" && (s.p95_latency_ms ?? 0) > 1000} />
                  <Metric label="req/min" value={`${s.requests_per_min}`} />
                </div>
              )}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title={`${releaseService} releases`} icon={<GitCommit className="h-4 w-4" />}>
        <ul className="flex flex-col gap-1.5 text-xs">
          {releases.map((r) => (
            <li key={r.version} className="flex items-baseline gap-2">
              <span className={cn("w-8 font-mono", r.live ? "text-accent" : "text-muted-foreground")}>
                {r.version}
              </span>
              <span className="flex-1 truncate">{r.message}</span>
              <span className="text-muted-foreground">{ago(r.deployed_at)}</span>
              {r.live && <Badge tone="accent">live</Badge>}
            </li>
          ))}
        </ul>
        {cloud.deployments.length > 0 && (
          <div className="mt-3 border-t border-border pt-3">
            {cloud.deployments.map((d) => (
              <p key={d.id} className="flex items-center justify-between text-xs">
                <span>
                  Rollback {d.service} {d.from_version} → {d.to_version}
                </span>
                <Badge tone={d.status === "succeeded" ? "success" : d.status === "failed" ? "destructive" : "warning"}>
                  {d.status.replace("_", " ")}
                </Badge>
              </p>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Audit trail" icon={<ScrollText className="h-4 w-4" />}>
        {cloud.audit.length === 0 ? (
          <p className="text-xs text-muted-foreground">Every tool call, approval and voice session event lands here.</p>
        ) : (
          <ul className="flex max-h-48 flex-col gap-1 overflow-y-auto font-mono text-[11px]">
            {cloud.audit.map((a, i) => (
              <li key={`${a.at}-${i}`} className="flex justify-between gap-2">
                <span className={cn(a.action.includes("approval") && "text-warning")}>{a.action}</span>
                <span className="text-muted-foreground">{a.actor}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function Metric({ label, value, bad = false }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="rounded-md bg-muted/60 px-2 py-1">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("font-mono", bad && "text-destructive")}>{value}</p>
    </div>
  );
}

function Panel({
  title,
  icon,
  action,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border bg-card/40 p-4">
      <header className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          {icon}
          {title}
        </h2>
        {action}
      </header>
      {children}
    </section>
  );
}
