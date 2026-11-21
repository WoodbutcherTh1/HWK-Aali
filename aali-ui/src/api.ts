import type { ActivityEvent, DoneResult, Proposal } from "./types";

const API_BASE_KEY = "aali_ui_api_base";
const API_KEY_STORAGE = "aali_ui_api_key";
const SID_KEY = "aali_ui_sid";

function envBase(): string {
  const value = import.meta.env.VITE_API_BASE;
  return typeof value === "string" ? value : "";
}

export function getApiBase(): string {
  return (localStorage.getItem(API_BASE_KEY) || envBase() || "").replace(/\/+$/, "");
}

export function setApiBase(value: string): void {
  const next = value.trim().replace(/\/+$/, "");
  if (next) localStorage.setItem(API_BASE_KEY, next);
  else localStorage.removeItem(API_BASE_KEY);
}

export function getApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE) || import.meta.env.VITE_API_KEY || "";
}

export function setApiKey(value: string): void {
  const next = value.trim();
  if (next) localStorage.setItem(API_KEY_STORAGE, next);
  else localStorage.removeItem(API_KEY_STORAGE);
}

function apiUrl(path: string): string {
  return `${getApiBase()}${path}`;
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const out: Record<string, string> = { ...extra };
  const key = getApiKey();
  if (key) out["X-API-Key"] = key;
  return out;
}

export function initialSid(): string {
  const fromUrl = new URLSearchParams(window.location.search).get("sid") || "";
  return fromUrl || localStorage.getItem(SID_KEY) || "";
}

export function persistSid(sid: string): void {
  if (!sid) return;
  localStorage.setItem(SID_KEY, sid);
  const url = new URL(window.location.href);
  url.searchParams.set("sid", sid);
  window.history.replaceState({}, "", url);
}

export function clearSid(): void {
  localStorage.removeItem(SID_KEY);
  const url = new URL(window.location.href);
  url.searchParams.delete("sid");
  window.history.replaceState({}, "", url);
}

export async function health(): Promise<boolean> {
  try {
    const res = await fetch(apiUrl("/api/health"), {
      headers: headers({ Accept: "application/json" }),
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}

export interface StreamOptions {
  sid?: string;
  signal?: AbortSignal;
  model?: string;
  onEvent?: (eventName: string, payload: unknown) => void;
}

export async function streamAsk(message: string, opts: StreamOptions = {}): Promise<DoneResult> {
  // Choose endpoint based on model
  const isFreeModel = opts.model === "free";
  const endpoint = isFreeModel ? "/api/ask-free" : "/api/ask/stream";
  const acceptHeader = isFreeModel ? "application/json" : "text/event-stream";

  const res = await fetch(apiUrl(endpoint), {
    method: "POST",
    headers: headers({ "Content-Type": "application/json", Accept: acceptHeader }),
    body: JSON.stringify({
      message,
      sid: opts.sid || undefined,
      mode: isFreeModel ? "cloud" : "local",
      model: opts.model || "local",
      policy: "auto",
      confirm: false,
      verbose: false,
    }),
    signal: opts.signal,
  });

  if (!res.ok) {
    let serverError = "";
    try {
      const body = (await res.json()) as { error?: string };
      serverError = String(body.error || "");
    } catch {
      // The body may be empty or non-JSON; the status is still useful.
    }
    const err = new Error(serverError || `stream HTTP ${res.status}`) as Error & {
      status?: number;
      serverError?: string;
    };
    err.status = res.status;
    err.serverError = serverError;
    throw err;
  }
  if (isFreeModel) {
    // /api/ask-free answers with ONE plain JSON body (no SSE framing). Running
    // the stream parser over it yields zero frames, so the "done" event never
    // arrives and the call throws "stream ended without a done event" even
    // though the server answered. Read the JSON and shape it like a done.
    const body = (await res.json()) as DoneResult;
    if (body.sid) persistSid(body.sid);
    return { ok: body.ok !== false, reply: body.reply, sid: body.sid, error: body.error };
  }

  if (!res.body) throw new Error("stream response has no body");

  const state: { done: DoneResult | null } = { done: null };
  await readSse(res.body, (eventName, payload) => {
    opts.onEvent?.(eventName, payload);
    if (eventName === "done") state.done = payload as DoneResult;
  });

  const done = state.done;
  if (!done) throw new Error("stream ended without a done event");
  if (done.sid) persistSid(done.sid);
  return done;
}

export class FeedUnavailableError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "FeedUnavailableError";
    this.status = status;
  }
}

/** The owner-wide live feed. It is open in local mode and admin-gated in key mode. */
export async function openWorkspaceFeed(
  signal: AbortSignal,
  onEvent: (payload: ActivityEvent) => void,
  onOpen?: () => void,
): Promise<void> {
  const res = await fetch(apiUrl("/api/brain/stream"), {
    headers: headers({ Accept: "text/event-stream" }),
    signal,
    cache: "no-store",
  });
  if (!res.ok) {
    throw new FeedUnavailableError(`feed HTTP ${res.status}`, res.status);
  }
  onOpen?.();
  if (!res.body) throw new FeedUnavailableError("feed response has no body");
  await readSse(res.body, (_eventName, payload) => {
    if (payload && typeof payload === "object") onEvent(payload as ActivityEvent);
  });
}

export async function fetchPendingProposals(): Promise<Proposal[]> {
  const res = await fetch(apiUrl("/api/proposals?status=pending&limit=20"), {
    headers: headers({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!res.ok) return [];
  const data = (await res.json()) as { ok?: boolean; proposals?: Proposal[] };
  return Array.isArray(data.proposals) ? data.proposals : [];
}

export async function fetchProposal(id: string): Promise<Proposal | null> {
  const res = await fetch(apiUrl(`/api/proposals/${encodeURIComponent(id)}`), {
    headers: headers({ Accept: "application/json" }),
    cache: "no-store",
  });
  if (!res.ok) return null;
  const data = (await res.json()) as { ok?: boolean; proposal?: Proposal };
  return data.proposal || null;
}

export interface ResolveResult {
  ok: boolean;
  status: number;
  error?: string;
  proposal?: Proposal;
}

export async function resolveProposal(id: string, approve: boolean): Promise<ResolveResult> {
  const res = await fetch(apiUrl("/api/approve"), {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ proposal_id: id, approve }),
  });
  let body: { error?: string; proposal?: Proposal } = {};
  try {
    body = (await res.json()) as { error?: string; proposal?: Proposal };
  } catch {
    // Keep the status-based error.
  }
  return {
    ok: res.ok,
    status: res.status,
    error: body.error || (res.ok ? undefined : `HTTP ${res.status}`),
    proposal: body.proposal,
  };
}

/**
 * Parse an SSE byte stream. Handles CRLF, comments, multi-line data, and
 * unknown event names. The callback is intentionally synchronous; consumers
 * that need React state updates can batch them themselves.
 */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onFrame: (eventName: string, payload: unknown) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const emit = (frame: string) => {
    let eventName = "message";
    const dataLines: string[] = [];
    for (const rawLine of frame.split("\n")) {
      const line = rawLine.replace(/\r$/, "");
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!dataLines.length) return;
    const raw = dataLines.join("\n");
    let payload: unknown = raw;
    try {
      payload = JSON.parse(raw);
    } catch {
      // A non-JSON data line is still useful to the caller as text.
    }
    onFrame(eventName, payload);
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let separator: number;
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      emit(frame);
    }
  }
  buffer += decoder.decode().replace(/\r\n/g, "\n");
  if (buffer.trim()) emit(buffer);
}
