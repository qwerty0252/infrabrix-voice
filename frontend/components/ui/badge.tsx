import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({
  className,
  tone = "muted",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: "muted" | "accent" | "success" | "warning" | "destructive" }) {
  const tones: Record<string, string> = {
    muted: "bg-muted text-muted-foreground",
    accent: "bg-accent/15 text-accent",
    success: "bg-[hsl(var(--success))]/15 text-[hsl(var(--success))]",
    warning: "bg-[hsl(var(--warning))]/15 text-[hsl(var(--warning))]",
    destructive: "bg-destructive/15 text-destructive",
  };
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", tones[tone], className)}
      {...props}
    />
  );
}
