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
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
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
export interface AaliRoleContext {
  role: "owner" | "dev" | "admin" | "user" | "guest";
  user_id: string;
  workspace_id: string;
  permissions: string[];
  previewing?: boolean;
}

/* Track B 11.7 — owner preview mode ("view as user"). Both directions
   carry BOTH credentials (master-key owners have no session token and
   vice versa); the server decides and never trusts the client. */
export async function previewStart(): Promise<boolean> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const t = getToken();
  const s = getSessionToken();
  if (t) headers["X-API-Key"] = t;
  if (s) headers["X-Session-Token"] = s;
  try {
    const res = await fetch(`${apiBase}/api/auth/preview`, {
      method: "POST", headers, body: "{}",
    });
    const data = await res.json().catch(() => ({}));
    return res.ok && !!data.ok;
  } catch {
    return false;
  }
}

export async function previewStop(): Promise<boolean> {
  const headers: Record<string, string> = {};
  const t = getToken();
  const s = getSessionToken();
  if (t) headers["X-API-Key"] = t;
  if (s) headers["X-Session-Token"] = s;
  try {
    const res = await fetch(`${apiBase}/api/auth/preview`, {
      method: "DELETE", headers,
    });
    const data = await res.json().catch(() => ({}));
    return res.ok && !!data.ok;
  } catch {
    return false;
  }
}

export async function authMe(): Promise<(MeInfo & { aali?: AaliRoleContext }) | null> {
  // Track B 11.3: send BOTH credentials (master key holders have no
  // session token; account users have no API key) — the server decides.
  const t = getToken();
  const s = getSessionToken();
  if (!t && !s) return null;
  const headers: Record<string, string> = {};
  if (t) headers["X-API-Key"] = t;
  if (s) headers["X-Session-Token"] = s;
  try {
    const res = await fetch(`${apiBase}/api/auth/me`, { headers });
    if (!res.ok) return null;
    const data = await res.json();
    return data.ok
      ? {
          email: data.email,
          role: data.role,
          is_admin: data.is_admin,
          aali: data.aali,
        }
      : null;
  } catch {
    return null;
  }
}
export function authLogout() {
  const t = getSessionToken();
  if (t) void fetch(`${apiBase}/api/auth/logout`, { method: "POST", headers: { "X-Session-Token": t } });
  setSessionToken("");
}

/* One-click admin handoff: mint a SINGLE-USE short-lived URL token and return
   the dashboard URL. The session token itself never appears in a URL or
   history — the dashboard trades ?ht= for a session server-side. */
export async function adminHandoff(): Promise<string | null> {
  try {
    const res = await fetch(`${apiBase}/api/auth/handoff`, {
      method: "POST",
      headers: { "X-Session-Token": getSessionToken() },
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !data.handoff_token) return null;
    return `${apiBase}/admin?ht=${encodeURIComponent(data.handoff_token)}`;
  } catch {
    return null;
  }
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
    /* 2026-09-24: verbose mode — live chain-of-thought + scratchpad. */
    verbose?: boolean;
    onCoT?: (text: string, iteration: number) => void;
    onScratchpad?: (content: string, lines: number) => void;
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
        verbose: opts?.verbose ?? false,
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
        } else if (eventName === "cot" && opts?.onCoT) {
          const p = payload as { text?: string; iteration?: number };
          opts.onCoT(String(p.text ?? ""), Number(p.iteration ?? 0));
        } else if (eventName === "scratchpad" && opts?.onScratchpad) {
          const p = payload as { content?: string; lines?: number };
          opts.onScratchpad(String(p.content ?? ""), Number(p.lines ?? 0));
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

/* — export conversation (Wave 1 #1, 2026-09-25) —
   Fetches as a blob so the API key never lands in a URL or history,
   then triggers a browser download. md = readable Markdown (default),
   json = faithful record dump for developers/backups. */
export async function exportSession(
  id: string,
  format: "md" | "json" = "md",
): Promise<void> {
  const res = await fetch(
    `${apiBase}/api/session/${encodeURIComponent(id)}/export?format=${format}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `aali-session-${
    id.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 24) || "chat"
  }.${format}`;
  a.click();
  URL.revokeObjectURL(url);
}

/* — search (Wave 1 #2, 2026-09-25) —
   Full-text search over the caller's conversations. The server decides
   scope (admin all / user own / guests 403) — the client never filters. */
export interface SearchHit {
  message_id: string;   // "<sid>:<turn_idx>"
  session_id: string;
  session_title: string;
  role: "user" | "assistant";
  snippet: string;      // plain text (safe)
  highlight: string;    // carries <mark> tags (escape before HTML use)
  timestamp: number;
  score: number;
}

export async function searchMessages(
  q: string,
  opts: { role?: string; from?: string; to?: string; session?: string;
          limit?: number; offset?: number } = {},
): Promise<{ total: number; results: SearchHit[] }> {
  const params = new URLSearchParams({ q });
  if (opts.role) params.set("role", opts.role);
  if (opts.from) params.set("from", opts.from);
  if (opts.to) params.set("to", opts.to);
  if (opts.session) params.set("session", opts.session);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  const res = await fetch(`${apiBase}/api/search?${params.toString()}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  const data = await res.json();
  return { total: data.total ?? 0, results: Array.isArray(data.results) ? data.results : [] };
}

/* ————— voice output (Wave 1 #3, 2026-09-25) —————
   POST /api/voice/synthesize returns a WAV blob; we play it via the
   Web Audio API and expose stop + settings. Arabic-first voices. */
let currentAudio: HTMLAudioElement | null = null;

export function stopSpeaking(): void {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
}

export async function speak(
  text: string,
  opts: { voice?: string; speed?: number; volume?: number } = {},
): Promise<boolean> {
  stopSpeaking();
  const res = await fetch(`${apiBase}/api/voice/synthesize`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice: opts.voice, speed: opts.speed }),
  });
  if (!res.ok) return false;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  if (typeof opts.volume === "number") audio.volume = Math.min(Math.max(opts.volume / 100, 0), 1);
  currentAudio = audio;
  audio.onended = () => {
    if (currentAudio === audio) currentAudio = null;
    URL.revokeObjectURL(url);
  };
  try {
    await audio.play();
    return true;
  } catch {
    // iOS Safari: playback needs a user gesture — auto-play may fail on
    // first touch-less attempt; surface false and let the caller toast.
    return false;
  }
}

/* ————— voice settings (Wave 1 #3.5-3.7) —————
   Persisted locally; the server keeps no per-user voice state yet. */
export interface VoiceSettings {
  autoPlay: boolean;
  voice: string;
  speed: number;   // 0.5 - 2.0
  volume: number;  // 0 - 100
}

const VOICE_KEY = "aali_voice";

export function getVoiceSettings(): VoiceSettings {
  try {
    const raw = localStorage.getItem(VOICE_KEY);
    if (raw) return { autoPlay: false, voice: "", speed: 1, volume: 100, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { autoPlay: false, voice: "", speed: 1, volume: 100 };
}

export function setVoiceSettings(s: VoiceSettings): void {
  try { localStorage.setItem(VOICE_KEY, JSON.stringify(s)); } catch { /* ignore */ }
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
