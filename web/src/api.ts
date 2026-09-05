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

export interface AskResult {
  ok: boolean;
  reply?: string;
  error?: string;
  sid?: string;
}

export async function ask(message: string): Promise<AskResult> {
  const res = await fetch(`${apiBase}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, sid: sid || undefined }),
  });
  const data: AskResult = await res.json();
  if (data.sid) {
    sid = data.sid;
    localStorage.setItem("aali_sid", sid);
  }
  return data;
}

export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}
