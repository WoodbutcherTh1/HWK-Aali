let apiBase = localStorage.getItem("aali_api") || "http://127.0.0.1:5055";

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

export type Policy = "auto" | "aggressive" | "always_ask";

export interface PendingAction {
  tool: string;
  arguments: Record<string, unknown>;
}

export interface AskResult {
  ok: boolean;
  reply?: string;
  error?: string;
  sid?: string;
  needs_confirm?: boolean;
  pending_action?: PendingAction;
}

export async function ask(
  message: string,
  opts?: { policy?: Policy; confirm?: boolean }
): Promise<AskResult> {
  const res = await fetch(`${apiBase}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      sid: sid || undefined,
      policy: opts?.policy ?? "auto",
      confirm: opts?.confirm ?? false,
    }),
  });
  const data: AskResult = await res.json();
  if (data.sid) {
    sid = data.sid;
    localStorage.setItem("aali_sid", sid);
  }
  return data;
}

export interface CompactResult {
  ok: boolean;
  compacted?: boolean;
  summary?: string;
  kept_turns?: number;
  message?: string;
  error?: string;
}

// Fold older turns of the current session into one summary, the same idea
// as Claude Code's own "compacting our conversation" step — keeps future
// requests small once a chat has grown long. Triggered by the "/compact"
// slash command.
export async function compactSession(): Promise<CompactResult> {
  if (!sid) return { ok: true, compacted: false, message: "لا توجد محادثة بعد." };
  const res = await fetch(`${apiBase}/api/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sid }),
  });
  return res.json();
}

export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}
