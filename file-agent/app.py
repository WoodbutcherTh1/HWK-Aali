"""Mobile-friendly Flask chat UI for the local Aali file agent.

Multi-turn conversations are stored server-side (sessions.jsonl) keyed by a
cookie, bounded to the last MAX_TURNS turns, with TTL cleanup. The model
receives system prompt + bounded history + the current message. No secrets are
ever written into the transcript or the log.

A JSON API at /api/ask lets other tools (e.g. n8n workflows) talk to the same
agent with a stable session id, so a conversation can span the chat UI and
any external workflow.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, make_response, render_template_string, request, send_from_directory

from agent_loop import AgentLoopError, agent_loop, compact_history

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET") or "hwk-local-dev-secret"


@app.after_request
def _cors_headers(response):
    """Allow the web/iOS/CLI clients (e.g. GitHub Pages site) to call the API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Session-Id"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

WORKSPACE_ROOT = Path(
    os.getenv("AGENT_WORKSPACE", Path(__file__).resolve().parent / "agent_workspace")
).expanduser().resolve()

SESSIONS_FILE = Path(__file__).resolve().parent / "sessions.jsonl"
MAX_TURNS = 100  # long conversation history kept on disk and shown in chat
SESSION_TTL_SECONDS = 30 * 24 * 3600  # sessions last 30 days
SID_COOKIE = "hwk_sid"

_sessions: dict[str, dict[str, object]] = {}


def _load_sessions() -> None:
    if not SESSIONS_FILE.exists():
        return
    try:
        with SESSIONS_FILE.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    _sessions[record["sid"]] = record
    except (json.JSONDecodeError, KeyError, OSError):
        pass


def _save_session(record: dict[str, object]) -> None:
    _sessions[record["sid"]] = record  # type: ignore[index]
    try:
        with SESSIONS_FILE.open("w", encoding="utf-8") as handle:
            for existing in _sessions.values():
                handle.write(json.dumps(existing, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _prune_sessions() -> None:
    now = time.time()
    stale = [sid for sid, record in _sessions.items() if now - float(record.get("updated_at", 0)) > SESSION_TTL_SECONDS]
    for sid in stale:
        _sessions.pop(sid, None)


def _get_session(sid: str | None) -> dict[str, object]:
    _prune_sessions()
    if sid and sid in _sessions:
        record = _sessions[sid]
        turns = record.get("turns")
        if not isinstance(turns, list):
            turns = []
            record["turns"] = turns
        return record
    record: dict[str, object] = {
        "sid": sid or uuid.uuid4().hex,
        "turns": [],
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    return record


def _append_turn(record: dict[str, object], role: str, content: str) -> None:
    turns = record["turns"]
    assert isinstance(turns, list)
    turns.append({"role": role, "content": content, "ts": time.time()})
    if len(turns) > MAX_TURNS:
        del turns[: len(turns) - MAX_TURNS]
    record["updated_at"] = time.time()
    _save_session(record)


def _history(record: dict[str, object]) -> list[dict[str, str]]:
    turns = record["turns"]
    assert isinstance(turns, list)
    return [
        {"role": str(turn["role"]), "content": str(turn["content"])}
        for turn in turns
        if turn.get("role") in ("user", "assistant")
    ]


PAGE = """\
<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>آلي — المساعد المحلي</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #0f172a; color: #e2e8f0; }
    main { width: min(760px, 100%); margin: auto; padding: 28px 16px 48px; }
    h1 { margin: 0 0 8px; font-size: clamp(1.7rem, 6vw, 2.4rem); }
    .subtitle { color: #94a3b8; line-height: 1.7; margin: 0 0 20px; }
    .chat { display: flex; flex-direction: column; gap: 12px; margin: 0 0 18px; }
    .bubble { padding: 12px 14px; border-radius: 14px; line-height: 1.75; white-space: pre-wrap; word-break: break-word; }
    .bubble.user { align-self: flex-start; background: #1e3a8a; border: 1px solid #3b82f6; }
    .bubble.assistant { align-self: flex-end; background: #111c33; border: 1px solid #334155; }
    .bubble .who { display: block; font-size: .78rem; color: #7dd3fc; margin-bottom: 4px; }
    form, .error { padding: 16px; border: 1px solid #334155; border-radius: 16px; background: #111c33; }
    label { display: block; margin-bottom: 8px; font-weight: 700; }
    textarea { width: 100%; min-height: 110px; resize: vertical; padding: 13px; border: 1px solid #475569; border-radius: 12px; background: #0b1220; color: #f8fafc; font: inherit; line-height: 1.6; }
    textarea:focus { outline: 2px solid #38bdf8; outline-offset: 2px; }
    button { width: 100%; margin-top: 14px; padding: 13px 18px; border: 0; border-radius: 11px; background: #38bdf8; color: #082f49; font: inherit; font-weight: 800; cursor: pointer; }
    button:hover { background: #7dd3fc; }
    .new-chat { background: #1e293b; color: #cbd5e1; border: 1px solid #475569; }
    .new-chat:hover { background: #334155; }
    .error { border-color: #f87171; color: #fecaca; }
    select { width: 100%; margin-bottom: 14px; padding: 12px; border: 1px solid #475569; border-radius: 12px; background: #0b1220; color: #f8fafc; font: inherit; }
    .meta { color: #64748b; font-size: .86rem; margin-top: 14px; }
    .empty { color: #64748b; text-align: center; padding: 22px 0; }
  </style>
</head>
<body>
  <main>
    <h1>آلي</h1>
    <p class="subtitle">مساعدك المحلي: محادثة طويلة + بناء وتعديل المشاريع داخل مجلد العمل، بدون API key.</p>
    {% if turns %}
    <section class="chat">
      {% for turn in turns %}
        <div class="bubble {{ turn.role }}">
          <span class="who">{{ 'أنت' if turn.role == 'user' else 'آلي' }}</span>{{ turn.content }}
        </div>
      {% endfor %}
    </section>
    {% else %}
    <p class="empty">ابدأ محادثة جديدة بالأسفل.</p>
    {% endif %}
    {% if error %}<section class="error">{{ error }}</section>{% endif %}
    <form method="post">
      <label for="mode">طريقة التشغيل</label>
      <select id="mode" name="mode">
        <option value="local" {% if mode == "local" %}selected{% endif %}>نموذج محلي / وضع بلا مفتاح</option>
        <option value="cloud" {% if mode == "cloud" %}selected{% endif %}>نموذج سحابي اختياري</option>
      </select>
      <label for="message">رسالتك</label>
      <textarea id="message" name="message" required placeholder="مثال: أنشئ ملف notes/today.txt واكتب بداخله مرحباً"></textarea>
      <button type="submit">إرسال</button>
    </form>
    <form method="post" action="/new">
      <button type="submit" class="new-chat">محادثة جديدة</button>
    </form>
    <p class="meta">مجلد العمل: {{ workspace }} — الملفات والأوامر تعمل داخل هذا المجلد فقط</p>
  </main>
</body>
</html>
"""


def _render(sid: str, record: dict[str, object], mode: str, error: str | None = None):
    response = make_response(
        render_template_string(
            PAGE,
            turns=record["turns"],
            error=error,
            mode=mode,
            workspace=WORKSPACE_ROOT,
        )
    )
    response.set_cookie(SID_COOKIE, sid, max_age=SESSION_TTL_SECONDS, httponly=True, samesite="Lax")
    return response


@app.route("/", methods=["GET", "POST"])
def home():
    sid = request.cookies.get(SID_COOKIE) or uuid.uuid4().hex
    record = _get_session(sid)
    if sid not in _sessions:
        _save_session(record)
    mode = "local"
    error = None
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        mode = request.form.get("mode", "local")
        if message:
            _append_turn(record, "user", message)
            try:
                response = agent_loop(
                    message,
                    WORKSPACE_ROOT,
                    mode=mode,
                    print_final=False,
                    history=_history(record),
                )
            except AgentLoopError as exc:
                response = f"[خطأ] {exc}"
                error = str(exc)
            _append_turn(record, "assistant", response)
    return _render(sid, record, mode, error)


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """JSON endpoint for external tools: POST {"message": ..., "sid": ..., "mode": ...}.

    Returns {"ok": true, "reply": ..., "sid": ...} on success, or {"ok": false,
    "error": ...} with status 400/500. The sid keeps multi-turn history across
    calls, matching the web UI's sessions.
    """
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return make_response({"ok": False, "error": "message is required"}, 400)
    sid = str(payload.get("sid") or request.headers.get("X-Session-Id") or uuid.uuid4().hex)
    mode = str(payload.get("mode", "local"))
    # Confirmation policy from the client: "auto" (default) / "aggressive" /
    # "always_ask". "confirm" is set True when the caller resends a message
    # the user just approved, bypassing the always_ask gate for this call.
    policy = str(payload.get("policy", "auto"))
    confirmed = bool(payload.get("confirm", False))
    # Which connector to use in cloud mode: "auto" (managed/OpenRouter,
    # unchanged default), or "openai"/"anthropic"/"gemini"/"openrouter" —
    # each connector reads its API key from an env var on this machine, so
    # picking one here never means sending a key through the chat.
    provider = str(payload.get("provider", "auto"))
    record = _get_session(sid)
    if sid not in _sessions:
        _save_session(record)
    _append_turn(record, "user", message)
    gate_state: dict[str, object] = {}
    try:
        reply = agent_loop(
            message,
            WORKSPACE_ROOT,
            mode=mode,
            print_final=False,
            history=_history(record),
            policy=policy,
            confirmed=confirmed,
            gate_state=gate_state,
            provider=provider,
        )
        ok = True
        status = 200
    except AgentLoopError as exc:
        reply = f"[خطأ] {exc}"
        ok = False
        status = 500
    _append_turn(record, "assistant", reply)
    body = {"ok": ok, "reply": reply, "sid": sid}
    if gate_state.get("blocked"):
        # A dangerous tool call was refused under "always_ask" this turn;
        # the client can show a confirm/cancel prompt and, on confirm,
        # resend the same message with {"confirm": true}.
        body["needs_confirm"] = True
        body["pending_action"] = {
            "tool": gate_state.get("tool"),
            "arguments": gate_state.get("arguments"),
        }
    response = make_response(body, status)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@app.route("/api/compact", methods=["POST"])
def api_compact():
    """Fold older turns of a session into one summary — the same idea as
    Claude Code's own conversation-compacting step. The frontend calls this
    from the "/compact" slash command; it can also be called any time a
    session has grown long, to keep future requests small and fast.
    """
    payload = request.get_json(silent=True) or {}
    sid = str(payload.get("sid") or request.headers.get("X-Session-Id") or "")
    if not sid:
        return make_response({"ok": False, "error": "sid is required"}, 400)
    record = _get_session(sid)
    new_history, summary = compact_history(_history(record))
    if summary is None:
        return {
            "ok": True,
            "compacted": False,
            "message": "المحادثة قصيرة بما يكفي، لا حاجة للاختصار.",
        }
    now = time.time()
    record["turns"] = [
        {"role": turn["role"], "content": turn["content"], "ts": now}
        for turn in new_history
    ]
    record["updated_at"] = now
    _save_session(record)
    return {
        "ok": True,
        "compacted": True,
        "summary": summary,
        "kept_turns": len(new_history),
    }


@app.route("/api/health", methods=["GET"])
def api_health():
    """Liveness probe for all clients: returns workspace and status."""
    return {"ok": True, "service": "aali", "workspace": str(WORKSPACE_ROOT)}


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@app.route("/ui/", defaults={"filename": "index.html"})
@app.route("/ui/<path:filename>")
def ui_client(filename: str):
    """Serve the modern Arabic-first web client (web/ directory)."""
    return send_from_directory(WEB_DIR, filename)


@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def openai_compat():
    """OpenAI-compatible chat endpoint so opencode / Cursor / Aider / Zed etc.
    can use Aali as their model: point the tool at
      base_url = http://<pc-ip>:5055/v1   (api_key: anything)
    Runs the full agent loop with tools; returns an OpenAI-shaped response.
    """
    if request.method == "OPTIONS":
        return make_response(("", 204))
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages") or []
    user_msgs = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return make_response({"error": {"message": "messages with a user turn are required"}}, 400)
    # Flatten multi-turn inputs into one agent request with history context.
    history = [
        {"role": str(m.get("role")), "content": str(m.get("content", ""))}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    message = user_msgs[-1]
    sid = request.headers.get("X-Session-Id") or uuid.uuid4().hex
    record = _get_session(sid)
    if sid not in _sessions:
        _save_session(record)
    try:
        reply = agent_loop(message, WORKSPACE_ROOT, mode="local", print_final=False, history=history)
        ok = True
    except AgentLoopError as exc:
        reply = f"[خطأ] {exc}"
        ok = False
    _append_turn(record, "user", message)
    _append_turn(record, "assistant", reply)
    import time as _time

    body = {
        "id": f"chatcmpl-{sid[:12]}",
        "object": "chat.completion",
        "created": int(_time.time()),
        "model": payload.get("model", "aali-local"),
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": reply},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    response = make_response(body, 200 if ok else 500)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@app.route("/new", methods=["POST"])
def new_conversation():
    sid = request.cookies.get(SID_COOKIE) or uuid.uuid4().hex
    record = _get_session(sid)
    record["turns"] = []
    record["updated_at"] = time.time()
    _save_session(record)
    return _render(sid, record, "local")


if __name__ == "__main__":
    _load_sessions()
    print(f"Aali chat UI starting on http://127.0.0.1:{os.getenv('PORT', '5000')}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)