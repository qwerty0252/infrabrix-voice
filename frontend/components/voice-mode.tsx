"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Mic, PhoneOff, ShieldCheck, Sparkles, Volume2, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { ApprovalState, TurnApproval, VoiceSessionStart } from "@/lib/types";
import { voiceApi } from "@/lib/voice-api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { VoiceOrb, type VoiceVisualState } from "@/components/voice-orb";

type VoiceModeProps = {
  projectId: string;
  conversationId: string | null;
  /** Called after every turn or decision so the page can refresh its panels. */
  onTurn: (userText: string, assistantText: string) => void;
};

type VoiceState = VoiceVisualState;
type ActivityTone = "active" | "success" | "warning" | "error" | "muted";
type VoiceActivity = { id: number; title: string; detail?: string; tone: ActivityTone };

const STATE_LABEL: Record<VoiceState, string> = {
  idle: "Ready",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Investigating",
  speaking: "Brix is speaking",
  error: "Needs attention",
};

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BACKOFF_MS = [1000, 2000, 4000];
const RECOVERY_POLL_MS = 3000;
const RECOVERY_POLL_LIMIT = 100;

type ApprovalCard = {
  actionId: string;
  tool: string;
  detail: string;
  phase: "awaiting" | "deciding" | "recovering" | "done";
  outcome?: string;
};

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function toolLabel(tool: string | null | undefined): string {
  if (!tool) return "project context";
  return tool.replaceAll("_", " ");
}

function base64FromBuffer(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

function bufferFromBase64(value: string): ArrayBuffer {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function toolArguments(value: unknown): Record<string, unknown> {
  if (typeof value === "string") {
    try {
      return JSON.parse(value) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function VoiceMode({ projectId, conversationId, onTurn }: VoiceModeProps) {
  const [state, setState] = useState<VoiceState>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string[]>([]);
  const [activity, setActivity] = useState<VoiceActivity[]>([]);
  const [inputLevel, setInputLevel] = useState(0);
  const [lastUserText, setLastUserText] = useState("");
  const [lastAssistantText, setLastAssistantText] = useState("");
  const [approval, setApproval] = useState<ApprovalCard | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const reconnectingRef = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);
  const sessionRef = useRef<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const audioSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const nextAudioTimeRef = useRef(0);
  const endingRef = useRef(false);
  const providerReadyRef = useRef(false);
  const nextActivityId = useRef(0);
  const inputLevelRef = useRef(0);
  const lastLevelUpdateRef = useRef(0);

  const addActivity = (title: string, tone: ActivityTone, detail?: string) => {
    const event = { id: ++nextActivityId.current, title, detail, tone };
    setActivity((items) => [
      ...items.map((item) =>
        item.tone === "active" ? { ...item, tone: "success" as const } : item,
      ).slice(-5),
      event,
    ]);
  };

  const stopPlayback = () => {
    for (const source of audioSourcesRef.current) source.stop();
    audioSourcesRef.current = [];
    nextAudioTimeRef.current = 0;
  };

  const cleanup = async (notifyServer: boolean) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "session.end" }));
      socket.close();
    }
    socketRef.current = null;
    workletRef.current = null;
    reconnectingRef.current = false;
    providerReadyRef.current = false;
    inputLevelRef.current = 0;
    setInputLevel(0);
    stopPlayback();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const context = contextRef.current;
    contextRef.current = null;
    if (context && context.state !== "closed") await context.close();
    const sessionId = sessionRef.current;
    sessionRef.current = null;
    if (notifyServer && sessionId) {
      try {
        await api.endVoiceSession(projectId, sessionId);
      } catch {
        // Local media is already closed; the server still enforces expiry.
      }
    }
  };

  const finish = async (notifyServer = true) => {
    if (endingRef.current) return;
    endingRef.current = true;
    await cleanup(notifyServer);
    setState("idle");
    addActivity("Voice session ended", "muted");
    endingRef.current = false;
  };

  const playAudio = (base64: string) => {
    const context = contextRef.current;
    if (!context) return;
    const pcm = new Int16Array(bufferFromBase64(base64));
    const audio = context.createBuffer(1, pcm.length, 24000);
    const data = audio.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) data[index] = pcm[index] / 0x8000;
    const source = context.createBufferSource();
    source.buffer = audio;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.03, nextAudioTimeRef.current);
    source.start(startAt);
    nextAudioTimeRef.current = startAt + audio.duration;
    const wasPlaying = audioSourcesRef.current.length > 0;
    audioSourcesRef.current.push(source);
    source.onended = () => {
      audioSourcesRef.current = audioSourcesRef.current.filter((node) => node !== source);
      if (audioSourcesRef.current.length === 0) setState("listening");
    };
    setState("speaking");
    if (!wasPlaying) addActivity("Brix is speaking", "active");
  };

  const sendToolResult = (callId: string, result: unknown) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(
        JSON.stringify({ type: "tool.result", call_id: callId, result: JSON.stringify(result) }),
      );
    }
  };

  const handleProviderEvent = async (payload: Record<string, unknown>) => {
    const type = String(payload.type ?? "");
    if (type === "session.ready") {
      const providerSessionId = String(
        payload.session_id ?? (payload.session as Record<string, unknown> | undefined)?.id ?? "",
      );
      if (!providerSessionId || !sessionRef.current) throw new Error("Voice provider did not send a session ID.");
      await api.connectVoiceSession(projectId, sessionRef.current, providerSessionId);
      providerReadyRef.current = true;
      reconnectAttemptRef.current = 0;
      reconnectingRef.current = false;
      setState("listening");
      addActivity("Voice session connected", "success", "Listening for your request");
      addActivity("Listening", "active", "Speak when you are ready");
      return;
    }
    if (type === "input.speech.started") {
      stopPlayback();
      setState("listening");
      addActivity("Listening", "active", "Brix will stop speaking while you talk");
      return;
    }
    if (type === "reply.audio" && typeof payload.audio === "string") {
      playAudio(payload.audio);
      return;
    }
    if (type === "transcript.user" || type === "transcript.agent") {
      const text = String(payload.text ?? payload.transcript ?? "").trim();
      if (text) {
        const speaker = type === "transcript.user" ? "You" : "Brix";
        setTranscript((items) => [...items.slice(-3), `${speaker}: ${text}`]);
        if (type === "transcript.user") setLastUserText(text);
      }
      return;
    }
    if (type === "tool.call") {
      const callId = String(payload.call_id ?? payload.tool_call_id ?? payload.id ?? "");
      const functionData = payload.function as Record<string, unknown> | undefined;
      const name = String(payload.name ?? payload.tool_name ?? functionData?.name ?? "");
      const rawArgs = payload.arguments ?? payload.parameters ?? functionData?.arguments;
      const message = toolArguments(rawArgs).message;
      if (name !== "brix_execute_turn" || !callId || typeof message !== "string" || !message.trim()) {
        addActivity("Blocked unsupported voice action", "error");
        sendToolResult(callId, { status: "failed", speech_text: "That voice action is not available." });
        return;
      }
      setState("thinking");
      setLastUserText(message);
      addActivity("Brix is investigating", "active", "Using your project context and safety policies");
      try {
        if (!sessionRef.current) throw new Error("Voice session ended.");
        const result = await api.executeVoiceTurn(projectId, sessionRef.current, callId, message);
        setLastAssistantText(result.speech_text);
        onTurn(message, result.speech_text);
        for (const event of result.display) {
          if (event.type === "tool_call") {
            addActivity(`Checking ${toolLabel(event.tool)}`, "active");
          } else if (event.type === "tool_result") {
            addActivity(
              event.ok ? `Completed ${toolLabel(event.tool)}` : `${toolLabel(event.tool)} needs attention`,
              event.ok ? "success" : "warning",
              event.summary ?? undefined,
            );
          } else if (event.type === "error") {
            addActivity("Brix stopped safely", "error", event.summary ?? undefined);
          }
        }
        const pending = result.approval as TurnApproval | null;
        if (pending?.confirmable && pending.action_id) {
          setApproval({
            actionId: pending.action_id,
            tool: pending.tool ?? "action",
            detail: pending.detail,
            phase: "awaiting",
          });
          setNotice(null);
          addActivity("Approval required", "warning", "Confirm on screen. Nothing has been executed");
          if (sessionRef.current) {
            void voiceApi.reportEvent(projectId, sessionRef.current, "approval_shown", pending.tool ?? undefined);
          }
        } else if (pending) {
          const approvalNotice = "This change cannot be approved from Voice Mode. Nothing was executed.";
          setNotice(approvalNotice);
          addActivity("Approval required", "warning", "No infrastructure action was executed");
        } else if (result.status === "completed") {
          addActivity("Brix completed the request", "success");
        } else if (result.status === "failed") {
          addActivity("Brix could not complete the request", "error", result.speech_text);
        }
        sendToolResult(callId, result);
      } catch (error) {
        const safe = error instanceof ApiError ? error.message : "Brix could not complete that request.";
        setLastAssistantText(safe);
        sendToolResult(callId, { status: "failed", speech_text: safe });
        setNotice(safe);
        addActivity("Brix could not complete the request", "error", safe);
      }
      return;
    }
    if (type === "session.error") {
      addActivity("Voice provider connection failed", "error");
      throw new Error("Voice provider connection failed.");
    }
    if (type === "session.ended") await finish(true);
  };

  const connectSocket = (credentials: VoiceSessionStart) => {
    const websocket = new WebSocket(`${credentials.websocket_url}?token=${encodeURIComponent(credentials.token)}`);
    socketRef.current = websocket;
    websocket.onopen = () => {
      addActivity("Connected to voice provider", "active", "Configuring the Brix bridge");
      websocket.send(JSON.stringify({ type: "session.update", session: credentials.session_config }));
    };
    websocket.onmessage = (event) => {
      try {
        void handleProviderEvent(JSON.parse(String(event.data)) as Record<string, unknown>).catch(() => {
          setNotice("Voice Mode could not complete a provider event.");
          addActivity("Voice provider event failed", "error");
          void finish(true);
        });
      } catch {
        setNotice("Voice Mode received an invalid provider response.");
        void finish(true);
      }
    };
    websocket.onerror = () => {
      addActivity("Voice provider connection problem", "warning");
    };
    websocket.onclose = (event) => {
      if (socketRef.current !== websocket) return; // replaced by a reconnect or ended locally
      if (endingRef.current || !sessionRef.current) return;
      void reconnect(`ws_close_${event.code}`);
    };
  };

  /** Re-redeem a provider token with bounded exponential backoff after an unexpected drop. */
  const reconnect = async (reason: string) => {
    if (reconnectingRef.current) return;
    reconnectingRef.current = true;
    providerReadyRef.current = false;
    stopPlayback();
    setState("connecting");
    while (reconnectAttemptRef.current < MAX_RECONNECT_ATTEMPTS) {
      const attempt = reconnectAttemptRef.current + 1;
      reconnectAttemptRef.current = attempt;
      const previous = sessionRef.current;
      if (endingRef.current || !previous) return;
      addActivity("Connection lost. Reconnecting", "warning", `Attempt ${attempt} of ${MAX_RECONNECT_ATTEMPTS}`);
      void voiceApi.reportEvent(projectId, previous, "reconnect_attempt", reason, attempt);
      await wait(RECONNECT_BACKOFF_MS[attempt - 1] ?? 4000);
      if (endingRef.current || sessionRef.current !== previous) return;
      try {
        const credentials = await voiceApi.reconnect(projectId, previous);
        if (endingRef.current) return;
        const stale = socketRef.current;
        socketRef.current = null;
        if (stale) {
          stale.onclose = null;
          stale.close();
        }
        sessionRef.current = credentials.session.id;
        reconnectingRef.current = false;
        connectSocket(credentials);
        return;
      } catch (error) {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) break; // not retryable
      }
    }
    reconnectingRef.current = false;
    if (sessionRef.current) void voiceApi.reportEvent(projectId, sessionRef.current, "disconnected", "reconnect_exhausted");
    setNotice("Voice connection was lost and could not be restored. Start Voice Mode again.");
    addActivity("Voice connection lost", "error");
    await finish(true);
    setState("error");
  };

  const pollRecovery = async (sessionId: string, actionId: string) => {
    for (let i = 0; i < RECOVERY_POLL_LIMIT; i += 1) {
      await wait(RECOVERY_POLL_MS);
      if (sessionRef.current !== sessionId || endingRef.current) return;
      let state: ApprovalState;
      try {
        state = await voiceApi.approvalStatus(projectId, sessionId, actionId);
      } catch {
        continue;
      }
      const recovery = state.recovery;
      if (!recovery || recovery.state === "in_progress") continue;
      const text =
        recovery.state === "recovered"
          ? "Recovery finished. The rollback deployed successfully."
          : "The rollback did not finish cleanly. Check the deployments panel.";
      setLastAssistantText(text);
      addActivity(recovery.state === "recovered" ? "Recovered" : "Recovery failed", recovery.state === "recovered" ? "success" : "error", text);
      setApproval((card) => (card && card.actionId === actionId ? { ...card, phase: "done", outcome: text } : card));
      return;
    }
  };

  const decideApproval = async (approved: boolean) => {
    const sessionId = sessionRef.current;
    if (!approval || !sessionId || approval.phase !== "awaiting") return;
    const { actionId } = approval;
    setApproval({ ...approval, phase: "deciding" });
    try {
      const result = await voiceApi.decideApproval(projectId, sessionId, actionId, approved);
      setLastAssistantText(result.speech_text);
      onTurn(approved ? "Approved on screen" : "Denied on screen", result.speech_text);
      if (result.status === "approved" && result.deployments.length > 0) {
        addActivity("Approved", "success", "Rollback started");
        setApproval((card) => (card ? { ...card, phase: "recovering", outcome: result.speech_text } : card));
        void pollRecovery(sessionId, actionId);
      } else {
        addActivity(
          result.status === "denied" ? "Denied" : "Action failed",
          result.status === "denied" ? "muted" : "error",
          result.speech_text,
        );
        setApproval((card) => (card ? { ...card, phase: "done", outcome: result.speech_text } : card));
      }
    } catch (error) {
      const safe = error instanceof ApiError ? error.message : "Could not record your decision.";
      setNotice(safe);
      addActivity("Approval could not be recorded", "error", safe);
      setApproval((card) => (card ? { ...card, phase: "awaiting" } : card));
    }
  };

  const start = async () => {
    if (!conversationId || (state !== "idle" && state !== "error")) return;
    setNotice(null);
    setTranscript([]);
    setActivity([]);
    setLastUserText("");
    setLastAssistantText("");
    setApproval(null);
    setInputLevel(0);
    setState("connecting");
    addActivity("Preparing secure voice session", "active", "Requesting microphone access");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true, channelCount: 1 },
      });
      streamRef.current = stream;
      const credentials = await api.createVoiceSession(projectId, { conversation_id: conversationId });
      sessionRef.current = credentials.session.id;
      addActivity("Secure session created", "success", "Connecting to the voice provider");
      const context = new AudioContext();
      contextRef.current = context;
      await context.resume();
      await context.audioWorklet.addModule("/voice/pcm-resampler.worklet.js");
      const source = context.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(context, "pcm-resampler", {
        processorOptions: { targetSampleRate: 24000 },
      });
      const silence = context.createGain();
      silence.gain.value = 0;
      source.connect(worklet).connect(silence).connect(context.destination);
      workletRef.current = worklet;
      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        const pcm = new Int16Array(event.data);
        let sum = 0;
        for (const sample of pcm) sum += Math.abs(sample) / 0x8000;
        const level = Math.min(1, (sum / Math.max(1, pcm.length)) * 7);
        inputLevelRef.current = inputLevelRef.current * 0.7 + level * 0.3;
        if (performance.now() - lastLevelUpdateRef.current > 80) {
          lastLevelUpdateRef.current = performance.now();
          setInputLevel(inputLevelRef.current);
        }
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN && sessionRef.current && providerReadyRef.current) {
          socket.send(JSON.stringify({ type: "input.audio", audio: base64FromBuffer(event.data) }));
        }
      };
      reconnectAttemptRef.current = 0;
      connectSocket(credentials);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Microphone access or Voice Mode setup failed.";
      setNotice(message);
      addActivity("Voice setup failed", "error", message);
      await finish(true);
      setState("error");
    }
  };

  useEffect(() => {
    endingRef.current = false; // React StrictMode re-runs effects after a simulated unmount
    const onPageHide = () => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: "session.end" }));
      }
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      endingRef.current = true; // stops reconnect and recovery polling after unmount
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "session.end" }));
        socket.close();
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (contextRef.current?.state !== "closed") void contextRef.current?.close();
      // Leaving the page unmounts this component; notify the authenticated
      // backend even though React cleanup itself cannot await.
      if (sessionRef.current) void api.endVoiceSession(projectId, sessionRef.current);
    };
  }, [projectId]);

  const active = state !== "idle" && state !== "error";
  const latestActivity = activity.at(-1);
  const statusCopy =
    state === "listening"
      ? "I’m listening. Say what you need help with."
      : state === "thinking"
        ? "Brix is checking your project context."
        : state === "speaking"
          ? "Brix is reading the response aloud."
          : state === "connecting"
            ? "Creating a secure voice connection."
            : state === "error"
              ? "Voice Mode needs attention. You can try again."
              : "Start a voice conversation with Brix.";
  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-card/30">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_34%,rgba(76,106,255,0.18),transparent_48%)]" />
      <div className="relative flex items-center justify-between px-4 py-3 sm:px-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className={`h-2 w-2 rounded-full ${active ? "bg-accent shadow-[0_0_12px_hsl(var(--accent))]" : "bg-muted-foreground"}`} />
          Voice conversation
        </div>
        <span className="text-xs text-muted-foreground">AssemblyAI Voice Agent API</span>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center px-4 pb-5 text-center">
        <div className="h-[min(40vh,360px)] w-[min(88vw,480px)] max-h-[360px]">
          <VoiceOrb state={state} inputLevel={inputLevel} />
        </div>
        <Badge tone={state === "error" ? "destructive" : state === "thinking" ? "warning" : "accent"} className="-mt-4">
          {state === "speaking" ? <Volume2 className="mr-1 h-3 w-3" /> : <Sparkles className="mr-1 h-3 w-3" />}
          {STATE_LABEL[state]}
        </Badge>
        <p className="mt-3 max-w-md text-sm text-muted-foreground">{statusCopy}</p>
        {latestActivity && active && (
          <p className="mt-2 text-xs text-muted-foreground">
            {latestActivity.title}{latestActivity.detail ? ` · ${latestActivity.detail}` : ""}
          </p>
        )}
      </div>

      {approval && (
        <div role="alertdialog" aria-label="Approval required" className="relative mx-4 mb-3 rounded-xl border border-warning/50 bg-warning/5 p-4 text-left sm:mx-6">
          <p className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck className="h-4 w-4" /> Approve {toolLabel(approval.tool)}?
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Brix will not do this from speech alone. Nothing has changed yet.
          </p>
          {approval.phase === "awaiting" || approval.phase === "deciding" ? (
            <div className="mt-3 flex gap-2">
              <Button type="button" size="sm" disabled={approval.phase === "deciding"} onClick={() => void decideApproval(true)}>
                <Check className="h-3.5 w-3.5" /> Approve
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={approval.phase === "deciding"} onClick={() => void decideApproval(false)}>
                <X className="h-3.5 w-3.5" /> Deny
              </Button>
            </div>
          ) : (
            <p className="mt-2 text-xs text-foreground">
              {approval.phase === "recovering" ? "Recovering. Watching the rollback deployment…" : approval.outcome}
            </p>
          )}
        </div>
      )}
      <div className="relative grid gap-3 border-t border-border bg-background/50 px-4 py-4 sm:grid-cols-[2fr_3fr] sm:px-6">
        <VoiceTranscript label="You" text={lastUserText || latestTranscript(transcript, "You:")} accent />
        <VoiceTranscript label="Brix" text={lastAssistantText || latestTranscript(transcript, "Brix:")} prominent />
      </div>
      <div className="relative flex flex-col items-center gap-2 border-t border-border px-4 py-4 sm:flex-row sm:justify-center">
        {active ? (
          <Button type="button" variant="destructive" onClick={() => void finish(true)}>
            <PhoneOff className="h-4 w-4" /> End voice conversation
          </Button>
        ) : (
          <Button type="button" size="lg" disabled={!conversationId} onClick={() => void start()}>
            <Mic className="h-4 w-4" /> {state === "error" ? "Try Voice Mode again" : "Start Voice Mode"}
          </Button>
        )}
        {notice && <p className="text-center text-xs text-destructive">{notice}</p>}
      </div>
    </section>
  );
}

function latestTranscript(items: string[], prefix: string): string {
  const value = [...items].reverse().find((item) => item.startsWith(prefix));
  return value?.slice(prefix.length).trim() ?? "";
}

function VoiceTranscript({
  label,
  text,
  accent = false,
  prominent = false,
}: {
  label: string;
  text: string;
  accent?: boolean;
  prominent?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-4 text-left ${accent ? "border-accent/30 bg-accent/5" : "border-border bg-card/60"} ${prominent ? "min-h-36" : "min-h-20"}`}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p
        aria-live={prominent ? "polite" : undefined}
        className={
          prominent
            ? "mt-2 max-h-56 overflow-y-auto text-base leading-7 text-foreground"
            : "mt-2 line-clamp-4 text-sm leading-6 text-foreground"
        }
      >
        {text || (label === "You" ? "Your spoken request will appear here." : "Brix’s spoken response will appear here.")}
      </p>
    </div>
  );
}
