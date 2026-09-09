/* آلي API client — plain /api/ask plus the SSE streaming endpoint the desktop
   app uses (live tool activity), and the sessions sidebar API. */

/* Default API base: when the UI is served by the Aali server itself (any port —
   5055, a busy-port fallback, a LAN address), talk to the same origin instead
   of a hardcoded 5055, so every deployment follows its real port. The classic
   http://127.0.0.1:5055 default stays for vite dev (port 5173, no proxy),
   https static hosting (GitHub Pages → visitor's localhost), and file:// —
   behavior unchanged there. */
export function defaultApiBase(): string {
  if (location.protocol === "http:" && location.port !== "5173") {
    return location.origin;
  }
  return "http://127.0.0.1:5055";
}

let apiBase = localStorage.getItem("aali_api") || defaultApiBase();

export function getApiBase() {
  return apiBase;
}
export function setApiBase(url: string) {
  apiBase = url.replace(/\/+$/, "");
  localStorage.setItem("aali_api", apiBase);
}

let sid = localStorage.getItem("aali_sid") || "";
export function getSid() {
  return sid;
}
export function clearSid() {
  sid = "";
  localStorage.removeItem("aali_sid");
}

/* Optional access token — when the server runs in multi-user mode
   (AALI_API_KEY set), every client must send it as X-API-Key. The token
   lives in this browser only, like the GitHub token. */
const TOKEN_KEY = "aali_api_token";
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t: string) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const t = getToken();
  if (t) h["X-API-Key"] = t;
  const s = getSessionToken();
  if (s) h["X-Session-Token"] = s;
  return h;
}

/* ————— Account auth (users | builders & team) —————
   The session token comes from /api/auth/login and lives per-browser. */
const SESSION_KEY = "aali_session";
export interface MeInfo { email: string; role: "user" | "admin"; is_admin: boolean }
export function getSessionToken(): string {
  return localStorage.getItem(SESSION_KEY) || "";
}
export function setSessionToken(t: string) {
  if (t) localStorage.setItem(SESSION_KEY, t);
  else localStorage.removeItem(SESSION_KEY);
}
export async function authSignup(email: string, password: string): Promise<{ ok: boolean; error?: string; dev_code?: string; role?: string }> {
  const res = await fetch(`${apiBase}/api/auth/signup`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return res.json();
}
export async function authVerify(email: string, code: string): Promise<{ ok: boolean; error?: string; role?: string }> {
  const res = await fetch(`${apiBase}/api/auth/verify`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });
  return res.json();
}
export async function authLogin(email: string, password: string): Promise<{ ok: boolean; error?: string; token?: string; role?: string }> {
  const res = await fetch(`${apiBase}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  if (data.ok && data.token) setSessionToken(data.token);
  return data;
}
export async function authResetRequest(email: string): Promise<{ ok: boolean; error?: string; dev_code?: string }> {
  const res = await fetch(`${apiBase}/api/auth/reset-request`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  return res.json();
}
export async function authResetConfirm(email: string, code: string, newPassword: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${apiBase}/api/auth/reset-confirm`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });
  return res.json();
}
export async function authMe(): Promise<MeInfo | null> {
  const t = getSessionToken();
  if (!t) return null;
  try {
    const res = await fetch(`${apiBase}/api/auth/me`, { headers: { "X-Session-Token": t } });
    if (!res.ok) return null;
    const data = await res.json();
    return data.ok ? { email: data.email, role: data.role, is_admin: data.is_admin } : null;
  } catch {
    return null;
  }
}
export function authLogout() {
  const t = getSessionToken();
  if (t) void fetch(`${apiBase}/api/auth/logout`, { method: "POST", headers: { "X-Session-Token": t } });
  setSessionToken("");
}

export type Policy = "auto" | "aggressive" | "always_ask";

/* — attachments: upload a file, then send its stored name with the next ask — */
export interface Attachment {
  stored: string;   // server-side name inside uploads/ (sent back with ask)
  name: string;     // friendly original name
  kind: string;     // image | video | audio | document | text | binary
  analysis?: Record<string, unknown>; // server-side extract (OCR, transcript…)
  preview?: string; // client-side object URL for image previews
}

export async function attach(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${apiBase}/api/attach`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data as Attachment;
}

export interface PendingAction {
  tool: string;
  arguments: Record<string, unknown>;
}

export interface ActivityEvent {
  event: string;
  tool?: string;
  arguments?: Record<string, string>;
  result?: string;
  provider?: string;
  model?: string;
}

export interface AskResult {
  ok: boolean;
  reply?: string;
  error?: string;
  sid?: string;
  needs_confirm?: boolean;
  pending_action?: PendingAction;
  suggestions?: string[];
}

async function persistSid(data: AskResult) {
  if (data.sid) {
    sid = data.sid;
    localStorage.setItem("aali_sid", sid);
  }
}

export async function ask(
  message: string,
  opts?: { policy?: Policy; confirm?: boolean; attachments?: string[] }
): Promise<AskResult> {
  const res = await fetch(`${apiBase}/api/ask`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      message,
      sid: sid || undefined,
      policy: opts?.policy ?? "auto",
      confirm: opts?.confirm ?? false,
      attachments: opts?.attachments,
    }),
  });
  const data: AskResult = await res.json();
  await persistSid(data);
  return data;
}

/* — streaming ask: returns the same shape as ask(), plus fires onActivity
   for every live tool event. Falls back to plain ask() if the stream fails
   (e.g. an older backend without /api/ask/stream). — */
export async function askStream(
  message: string,
  opts?: {
    policy?: Policy;
    confirm?: boolean;
    attachments?: string[];
    onActivity?: (ev: ActivityEvent) => void;
    signal?: AbortSignal;
  }
): Promise<AskResult> {
  let sawFrame = false;
  try {
    const res = await fetch(`${apiBase}/api/ask/stream`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        message,
        sid: sid || undefined,
        policy: opts?.policy ?? "auto",
        confirm: opts?.confirm ?? false,
        attachments: opts?.attachments,
      }),
      signal: opts?.signal,
    });
    if (!res.ok || !res.body) throw new Error(`stream HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done: AskResult | null = null;
    for (;;) {
      const { value, done: closed } = await reader.read();
      if (closed) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        let eventName = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) eventName = line.slice(7).trim();
          else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
          else if (line.startsWith("data:")) dataLines.push(line.slice(5));
        }
        if (!dataLines.length) continue;
        sawFrame = true;
        let payload: unknown;
        try {
          payload = JSON.parse(dataLines.join("\n"));
        } catch {
          continue;
        }
        if (eventName === "activity" && opts?.onActivity) {
          opts.onActivity(payload as ActivityEvent);
        } else if (eventName === "done") {
          done = payload as AskResult;
        }
      }
    }
    if (done) {
      await persistSid(done);
      return done;
    }
    throw new Error("stream ended without a done event");
  } catch (err) {
    if (opts?.signal?.aborted) throw err;
    // Fallback to the plain endpoint ONLY if the stream never delivered a
    // frame — otherwise a mid-stream hiccup would run the agent a SECOND time
    // and duplicate the reply (seen live 2026-09-07).
    if (sawFrame) throw err;
    return ask(message, opts);
  }
}

/* — sessions sidebar — */
export interface SessionRow {
  sid: string;
  title: string;
  turns: number;
  updated_at: number;
}

export async function listSessions(): Promise<SessionRow[]> {
  const res = await fetch(`${apiBase}/api/sessions`, { headers: authHeaders() });
  const data = await res.json();
  return Array.isArray(data.sessions) ? data.sessions : [];
}

export interface StoredTurn {
  role: string;
  content: string;
  ts?: number;
}

export async function getSession(id: string): Promise<StoredTurn[]> {
  const res = await fetch(`${apiBase}/api/session/${encodeURIComponent(id)}`, { headers: authHeaders() });
  const data = await res.json();
  return Array.isArray(data.turns) ? data.turns : [];
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${apiBase}/api/session/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
}

export interface CompactResult {
  ok: boolean;
  compacted?: boolean;
  summary?: string;
  kept_turns?: number;
  message?: string;
  error?: string;
}

// Fold older turns of the current session into one summary — keeps future
// requests small once a chat has grown long. Triggered by "/compact".
export async function compactSession(): Promise<CompactResult> {
  if (!sid) return { ok: true, compacted: false, message: "لا توجد محادثة بعد." };
  const res = await fetch(`${apiBase}/api/compact`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ sid }),
  });
  return res.json();
}

export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase}/api/health`, { headers: authHeaders() });
    return res.ok;
  } catch {
    return false;
  }
}
