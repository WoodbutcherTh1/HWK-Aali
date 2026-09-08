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
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import agent_log
from agent_log import new_request_id
from flask import Flask, Response, make_response, render_template_string, request, send_from_directory
from agent_loop import AgentLoopError, agent_loop, compact_history

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET") or "hwk-local-dev-secret"


# ————— multi-user foundation —————
# Aali runs as a SERVER that many clients (web, desktop PWA, CLI, terminal,
# external tools) talk to over HTTP. When AALI_API_KEY is set on the server,
# every /api/* call must present the same value as an X-API-Key header (or
# ?key= for simple GETs) — that key also ISOLATES sessions per user, so two
# people sharing one server never see each other's conversations.
# Unset (default): local single-user mode, unchanged behaviour.
from file_agent import apikeys

# The ADMIN key. Aali is the provider: clients authenticate with either this
# master key (admin) or a per-user key issued via the key platform. User keys
# are stored hashed in apikeys (never the admin key).
API_KEY = os.getenv("AALI_API_KEY", "").strip()
apikeys.ensure_loaded()


def _client_key() -> str:
    """Identity of the caller: the access token, or '' for local/anon mode."""
    return request.headers.get("X-API-Key", "") or request.args.get("key", "")


def _auth_state(presented: str | None = None) -> dict:
    """Resolve the caller's key: returns {is_admin, key_id, key} or None if
    unauthenticated. Admin = master AALI_API_KEY; user = an issued key."""
    key = presented if presented is not None else _client_key()
    if not key:
        return None
    if API_KEY and key == API_KEY:
        return {"is_admin": True, "key_id": "admin", "key": key}
    rec = apikeys.authenticate(key)
    if not rec:
        return None
    return {"is_admin": False, "key_id": rec["key_id"], "key": key}


def _user_ns(sid: str | None) -> str:
    """Session ids are stored PER USER: <ns>:<sid>. Local mode keeps the bare sid.
    Issued keys namespace by their key_id (the plaintext key never touches disk)."""
    if not API_KEY:
        return sid or ""
    auth = _auth_state()
    if auth and not auth["is_admin"]:
        return f"u{auth['key_id']}:{sid or uuid.uuid4().hex}"
    return f"u{_client_key()}:{sid}" if sid else f"u{_client_key()}:{uuid.uuid4().hex}"


@app.before_request
def _require_api_key():
    if request.method == "OPTIONS":
        return None
    # The OpenAI-compatible /v1 endpoint is a public API surface and is guarded
    # separately (it accepts Authorization: Bearer too). UI and static assets
    # stay public.
    if request.path.startswith("/v1/"):
        return None
    if not API_KEY:
        return None  # local single-user mode: open
    if request.path.startswith("/api/register"):
        return None  # self-serve signup is public (rate-limited inside)
    if not request.path.startswith("/api/"):
        return None
    auth = _auth_state()
    if not auth:
        return make_response({"ok": False, "error": "unauthorized: missing or wrong X-API-Key"}, 401)
    if not auth["is_admin"] and apikeys.over_daily_cap(auth["key_id"]):
        return make_response({"ok": False, "error": "daily request cap reached for this key"}, 429)
    # Meter usage (best-effort) for every authenticated /api call.
    apikeys.record_usage(auth["key_id"])
    return None


@app.after_request
def _cors_headers(response):
    """Allow the web/desktop/CLI clients (even from other origins) to call the API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Session-Id, X-API-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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
    key = str(record.get("key") or record["sid"])
    _sessions[key] = record  # type: ignore[index]
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
    client_sid = sid or uuid.uuid4().hex
    key = _user_ns(client_sid)
    record = _sessions.get(key)
    if record is not None:
        turns = record.get("turns")
        if not isinstance(turns, list):
            turns = []
            record["turns"] = turns
        return record
    record = {
        "key": key,
        "sid": client_sid,
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
    _meter_chars(len(message), 0)
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
    _meter_chars(0, len(reply))
    body = {"ok": ok, "reply": reply, "sid": sid}
    from file_agent import suggestions
    body["suggestions"] = suggestions.suggest(reply, message)
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


def _meter_chars(chars_in: int, chars_out: int) -> None:
    """Attribute request chars to the calling user key (admin/none = skip)."""
    if not API_KEY:
        return
    auth = _auth_state()
    if auth and not auth["is_admin"]:
        try:
            apikeys.record_chars(auth["key_id"], chars_in, chars_out)
        except Exception:  # noqa: BLE001
            pass


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


@app.route("/api/ask/stream", methods=["POST"])
def api_ask_stream():
    """Server-Sent Events version of /api/ask — the desktop app's main endpoint.

    While the agent works, live `activity` events (tool_requested /
    tool_result / provider_selected, relayed from the agent_log bus) stream to
    the client so the user sees WHAT Aali is doing in real time instead of a
    silent wait. The stream ends with one `done` event carrying the same body
    shape as /api/ask (reply, sid, suggestions, needs_confirm), so the client
    can fall back to the plain endpoint transparently.
    """
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return make_response({"ok": False, "error": "message is required"}, 400)
    sid = str(payload.get("sid") or request.headers.get("X-Session-Id") or uuid.uuid4().hex)
    mode = str(payload.get("mode", "local"))
    policy = str(payload.get("policy", "auto"))
    confirmed = bool(payload.get("confirm", False))
    provider = str(payload.get("provider", "auto"))

    record = _get_session(sid)
    if sid not in _sessions:
        _save_session(record)
    _append_turn(record, "user", message)

    request_id = new_request_id()
    events = agent_log.subscribe(request_id)
    result: dict[str, object] = {}
    gate_state: dict[str, object] = {}

    def _run() -> None:
        try:
            reply = agent_loop(
                message, WORKSPACE_ROOT, mode=mode, print_final=False,
                history=_history(record), policy=policy, confirmed=confirmed,
                gate_state=gate_state, provider=provider, request_id=request_id,
            )
            result["ok"], result["reply"] = True, reply
        except AgentLoopError as exc:
            result["ok"], result["reply"] = False, f"[خطأ] {exc}"
        except Exception as exc:  # noqa: BLE001
            result["ok"], result["reply"] = False, f"[خطأ] {exc}"
        reply = str(result.get("reply", ""))
        # The turn is recorded HERE, inside the worker thread, so the session
        # keeps the answer even if the client disconnects mid-stream.
        _append_turn(record, "assistant", reply)
        if result.get("ok"):
            from file_agent import suggestions
            result["suggestions"] = suggestions.suggest(reply, message)
        if gate_state.get("blocked"):
            result["needs_confirm"] = True
            result["pending_action"] = {
                "tool": gate_state.get("tool"),
                "arguments": gate_state.get("arguments"),
            }
        result["done"] = True

    worker = threading.Thread(target=_run, name=f"aali-ask-{request_id}", daemon=True)
    worker.start()

    ACTIVITY_EVENTS = ("provider_selected", "tool_requested", "tool_result")

    def _generate():
        last_beat = time.time()
        try:
            while True:
                try:
                    event = events.get(timeout=1.0)
                except queue.Empty:
                    now = time.time()
                    if now - last_beat >= 15:
                        last_beat = now
                        yield ": keep-alive\n\n"
                    if result.get("done"):
                        break
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") in ACTIVITY_EVENTS:
                    data = {
                        k: event.get(k)
                        for k in ("event", "tool", "arguments", "result", "provider", "model")
                        if event.get(k) is not None
                    }
                    # Keep the wire small; arguments/results are display-only.
                    if isinstance(data.get("arguments"), dict):
                        data["arguments"] = {
                            str(k): str(v)[:160]
                            for k, v in list(data["arguments"].items())[:6]
                        }
                    if "result" in data:
                        data["result"] = str(data["result"])[:400]
                    yield (
                        "event: activity\ndata: "
                        + json.dumps(data, ensure_ascii=False) + "\n\n"
                    )
                if result.get("done") and events.empty():
                    break
            body = {
                "ok": bool(result.get("ok")),
                "reply": str(result.get("reply", "…")),
                "sid": sid,
            }
            if "suggestions" in result:
                body["suggestions"] = result["suggestions"]
            if result.get("needs_confirm"):
                body["needs_confirm"] = True
                body["pending_action"] = result.get("pending_action")
            yield "event: done\ndata: " + json.dumps(body, ensure_ascii=False) + "\n\n"
        finally:
            agent_log.unsubscribe(request_id)

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_sessions_loaded = False


def _ensure_sessions_loaded() -> None:
    global _sessions_loaded
    if not _sessions_loaded:
        _load_sessions()
        _sessions_loaded = True


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    """Sidebar data: one row per session (title = first user message).
    In multi-user mode only the calling user's sessions are listed."""
    _ensure_sessions_loaded()
    prefix = f"u{_client_key()}:" if API_KEY else ""
    rows = []
    for key, rec in _sessions.items():
        if API_KEY and not key.startswith(prefix):
            continue
        turns = rec.get("turns") if isinstance(rec.get("turns"), list) else []
        title = next(
            (str(t.get("content", ""))[:70] for t in turns if t.get("role") == "user"),
            "محادثة",
        )
        rows.append(
            {
                "sid": str(rec.get("sid") or key),
                "title": title,
                "turns": len(turns),
                "updated_at": float(rec.get("updated_at", 0)),
            }
        )
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return {"ok": True, "sessions": rows[:50]}


@app.route("/api/session/<sid>", methods=["GET"])
def api_session_get(sid: str):
    _ensure_sessions_loaded()
    rec = _sessions.get(_user_ns(sid))
    if not rec:
        return make_response({"ok": False, "error": "session not found"}, 404)
    return {"ok": True, "sid": sid, "turns": rec.get("turns", [])}


@app.route("/api/session/<sid>", methods=["DELETE"])
def api_session_delete(sid: str):
    _ensure_sessions_loaded()
    key = _user_ns(sid)
    if key not in _sessions:
        return make_response({"ok": False, "error": "session not found"}, 404)
    _sessions.pop(key, None)
    try:
        with SESSIONS_FILE.open("w", encoding="utf-8") as handle:
            for existing in _sessions.values():
                handle.write(json.dumps(existing, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return {"ok": True}


@app.route("/api/health", methods=["GET"])
def api_health():
    """Liveness probe for all clients: returns workspace and status."""
    return {"ok": True, "service": "aali", "workspace": str(WORKSPACE_ROOT)}


# ————— Aali as a provider: key platform + admin —————

def _require_admin():
    """Return None when the caller presented the admin master key, else a 401."""
    if not API_KEY:
        return None  # local single-user mode: open admin (localhost tooling)
    auth = _auth_state()
    if not auth or not auth["is_admin"]:
        return make_response({"ok": False, "error": "admin key required"}, 401)
    return None


@app.route("/api/register", methods=["POST"])
def api_register():
    """Self-serve signup: POST {"label": "..."} -> {"ok": true, "api_key": "aali-..."}.
    Rate-limited per IP; disable entirely with AALI_OPEN_SIGNUP=0."""
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("label", ""))[:80]
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    key_id, plaintext, err = apikeys.signup(label, ip)
    if err:
        return make_response({"ok": False, "error": err}, 429)
    return {"ok": True, "key_id": key_id, "api_key": plaintext,
            "note": "احفظ هذا المفتاح — يُعرض مرة واحدة فقط"}


@app.route("/api/admin/keys", methods=["GET"])
def api_admin_keys_list():
    denied = _require_admin()
    if denied:
        return denied
    return {"ok": True, "keys": apikeys.list_keys(), "stats": apikeys.stats()}


@app.route("/api/admin/keys", methods=["POST"])
def api_admin_keys_issue():
    """Issue a key: POST {"label": "client-name"} (admin only)."""
    denied = _require_admin()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("label", ""))[:80]
    key_id, plaintext = apikeys.issue_key(label=label, created_by="admin")
    return {"ok": True, "key_id": key_id, "api_key": plaintext,
            "note": "احفظ هذا المفتاح — يُعرض مرة واحدة فقط"}


@app.route("/api/admin/keys/<key_id>", methods=["DELETE"])
def api_admin_keys_revoke(key_id: str):
    denied = _require_admin()
    if denied:
        return denied
    if not apikeys.revoke(key_id):
        return make_response({"ok": False, "error": "key not found"}, 404)
    return {"ok": True, "revoked": key_id}

# ————— شجرة عقل آلي (المالك فقط) —————

@app.route("/brain")
def brain_page():
    """LIVE tree of how Aali works — owner/admin only, never linked for users."""
    denied = _require_admin()
    if denied:
        return denied
    from file_agent import brain_tree
    return brain_tree.render_html(brain_tree.live_snapshot())


@app.route("/api/brain/live", methods=["GET"])
def api_brain_live():
    """JSON version of the tree: full structure + live counters (admin only)."""
    denied = _require_admin()
    if denied:
        return denied
    from file_agent import brain_tree
    return {
        "ok": True,
        "flows": brain_tree.FLOWS,
        "sources": brain_tree.SOURCES,
        "security": brain_tree.SECURITY_NOTES,
        "live": brain_tree.live_snapshot(),
    }


@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    denied = _require_admin()
    if denied:
        return denied
    _ensure_sessions_loaded()
    admin_prefix = "uadmin:"
    user_sessions = sum(
        1 for key in _sessions
        if key.startswith("u") and not key.startswith(admin_prefix)
    )
    return {"ok": True, "keys": apikeys.stats(), "user_sessions": user_sessions,
            "workspace": str(WORKSPACE_ROOT),
            "brain": _brain_summary()}


def _promoted_model() -> dict | None:
    """Aali's own promoted model, if one exists: read from the file the soup
    pipeline writes when its tuned adapter beats the baseline on the exam."""
    marker = Path(os.getenv("AALI_PROMOTED_FILE", "D:/hwk-data/soup/promoted.json"))
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data or not data.get("adapter_dir"):
        return None
    return data


def _brain_summary() -> dict:
    """Which brain is Aali serving right now (own model first)."""
    promoted = _promoted_model()
    if promoted:
        return {"provider": "aali_own", "detail": promoted}
    if os.getenv("AALI_OLLAMA", "1") != "0":
        try:
            import requests as _rq
            if _rq.get(agent_loop.OLLAMA_URL + "/api/tags", timeout=2).ok:
                return {"provider": "ollama", "detail": agent_loop.OLLAMA_MODEL,
                        "note": "interim brain until Aali's own model is promoted"}
        except Exception:  # noqa: BLE001
            pass
    return {"provider": "scratch_model", "detail": "local checkpoint"}


@app.route("/admin")
def admin_dashboard():
    """Admin dashboard — full control like other AI-provider consoles.
    Self-contained page (no rebuild); talks to /api/admin/* with the master
    key. Set ?demo=1 to see it without a key in local single-user mode."""
    html = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>آلي — لوحة التحكم</title>
<style>
  :root{--bg:#0f1117;--panel:#171a23;--line:#262b38;--fg:#e6e8ee;--mut:#9aa0ae;--acc:#4f8cff;--ok:#2ecc71;--bad:#e74c3c}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,'Tajawal',sans-serif;background:var(--bg);color:var(--fg)}
  header{display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid var(--line)}
  header h1{font-size:18px;margin:0}
  header .sub{color:var(--mut);font-size:12px}
  main{padding:22px;max-width:1100px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
  .card .k{color:var(--mut);font-size:12px}
  .card .v{font-size:22px;font-weight:700;margin-top:4px}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:20px}
  .panel h2{font-size:15px;margin:0 0 12px}
  input,button{font:inherit;padding:9px 12px;border-radius:8px;border:1px solid var(--line);background:#0f1117;color:var(--fg)}
  input{flex:1;min-width:0}
  button{cursor:pointer;border:1px solid var(--line)}
  button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
  button.danger{background:transparent;border-color:var(--bad);color:var(--bad)}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:right;padding:8px;border-bottom:1px solid var(--line)}
  th{color:var(--mut);font-weight:600}
  .mono{font-family:ui-monospace,monospace;font-size:12px;color:var(--mut)}
  .tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
  .tag.ok{background:rgba(46,204,113,.15);color:var(--ok)}
  .tag.off{background:rgba(231,76,60,.15);color:var(--bad)}
  .bar{height:6px;background:#0f1117;border-radius:999px;overflow:hidden}
  .bar>i{display:block;height:100%;background:var(--acc)}
  .muted{color:var(--mut);font-size:12px}
  .hidden{display:none}
  #secret{word-break:break-all;background:rgba(79,140,255,.1);border:1px solid var(--acc);padding:10px;border-radius:8px;font-family:ui-monospace,monospace;font-size:13px}
  /* in-page confirm dialog (webview-safe replacement for window.confirm) */
  .dlg-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:50}
  .dlg{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;max-width:340px;width:90%}
  .dlg p{margin:0 0 16px;line-height:1.7}
  .dlg .row{justify-content:flex-start}
</style>
</head>
<body>
<header>
  <h1>آلي · لوحة التحكم</h1>
  <div class="sub" id="brain">…</div>
</header>
<main>
  <div class="panel">
    <div class="row"><label>مفتاح المدير (X-API-Key)</label>
      <input type="password" id="adminkey" placeholder="أدخل AALI_API_KEY">
      <button class="primary" onclick="load()">دخول</button>
    </div>
    <div class="muted" style="margin-top:8px">المفتاح يُحفظ في متصفحك فقط (localStorage) ولا يُرسل إلا لعنوان هذا الخادم.</div>
  </div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>إصدار مفتاح جديد</h2>
    <div class="row"><input id="label" placeholder="اسم العميل (مثال: تطبيق ويب)"><button class="primary" onclick="issue()">إصدار</button></div>
    <div id="secret" class="hidden"></div>
  </div>

  <div class="panel">
    <h2>المفاتيح</h2>
    <table><thead><tr><th>العميل</th><th>المفتاح</th><th>الطلبات</th><th>أحرف</th><th>آخر استخدام</th><th>الحالة</th><th></th></tr></thead>
    <tbody id="keys"></tbody></table>
  </div>
</main>
<div class="dlg-backdrop hidden" id="dlg">
  <div class="dlg">
    <p id="dlgmsg"></p>
    <div class="row">
      <button class="primary" id="dlgok">تأكيد</button>
      <button class="ghost" id="dlgcancel">تراجع</button>
    </div>
  </div>
</div>
<script>
const $=id=>document.getElementById(id);
function api(method,path,body,key){
  return fetch(path,{method,headers:{'Content-Type':'application/json','X-API-Key':key||$('adminkey').value.trim()},body:body?JSON.stringify(body):undefined}).then(r=>r.json());
}
function fmt(t){ if(!t) return '—'; const d=new Date(t*1000); return d.toLocaleString('ar'); }
function load(){
  localStorage.setItem('aali_admin_token',$('adminkey').value.trim());
  api('GET','/api/admin/stats').then(s=>{
    if(!s.ok){uiAlert(s.error||'فشل الدخول');return;}
    render(s);
  });
}
function render(s){
  $('brain').textContent = s.brain ? ('العقل: '+s.brain.provider+(s.brain.detail&&typeof s.brain.detail==='object'?(s.brain.detail.adapter_dir||''):'') ) : '';
  const k=s.keys||{};
  const cards=[
    ['مفاتيح نشطة',k.active_keys],['إجمالي المفاتيح',k.total_keys],['طلبات',k.total_requests],['أحرف',k.total_chars],['جلسات مستخدمين',s.user_sessions]
  ];
  $('cards').innerHTML=cards.map(c=>'<div class="card"><div class="k">'+c[0]+'</div><div class="v">'+c[1]+'</div></div>').join('');
  api('GET','/api/admin/keys').then(r=>{
    if(!r.ok) return;
    $('keys').innerHTML=(r.keys||[]).map(x=>
      '<tr data-kid="'+x.key_id+'"><td>'+ (x.label||'—') +'</td><td class="mono">'+x.prefix+'…</td><td>'+x.requests+'</td><td>'+x.chars_in+'+'+x.chars_out+'</td><td>'+fmt(x.last_used_at)+'</td><td><span class="tag '+(x.revoked?'off':'ok')+'">'+(x.revoked?'مُلغى':'نشط')+'</span></td><td>'+(x.revoked?'':'<button class="danger" data-revoke="1">إلغاء</button>')+'</td></tr>'
    ).join('') || '<tr><td colspan="7" class="muted">لا مفاتيح بعد</td></tr>';
  });
}
document.addEventListener('click',function(e){
  const btn=e.target.closest('button[data-revoke]');
  if(!btn) return;
  const tr=btn.closest('tr');
  if(tr) askRevoke(tr.getAttribute('data-kid'), tr.querySelector('td'));
});
/* In-page confirm/alert: window.confirm and window.alert BLOCK embedded
   webviews (the whole page freezes until the host answers the dialog), so
   the dashboard ships its own. uiConfirm falls back to window.confirm only
   if the dialog elements are missing. */
function uiConfirm(msg){
  const dlg=$('dlg');
  if(!dlg) return Promise.resolve(window.confirm(msg));
  $('dlgmsg').textContent=msg;
  dlg.classList.remove('hidden');
  return new Promise(function(resolve){
    function done(v){ dlg.classList.add('hidden'); $('dlgok').onclick=$('dlgcancel').onclick=null; resolve(v); }
    $('dlgok').onclick=function(){ done(true); };
    $('dlgcancel').onclick=function(){ done(false); };
  });
}
function uiAlert(msg){
  const dlg=$('dlg');
  if(!dlg){ window.alert(msg); return; }
  $('dlgmsg').textContent=msg;
  $('dlgcancel').classList.add('hidden');
  dlg.classList.remove('hidden');
  $('dlgok').onclick=function(){ dlg.classList.add('hidden'); $('dlgcancel').classList.remove('hidden'); };
}
function askRevoke(id,labelCell){
  const label = labelCell ? labelCell.textContent : '';
  uiConfirm('إلغاء مفتاح «'+label+'»؟ لن يعود بعدو صالحًا.').then(function(yes){
    if(yes) doRevoke(id);
  });
}
function doRevoke(id){
  api('DELETE','/api/admin/keys/'+id).then(function(r){ if(r.ok) load(); else uiAlert(r.error||'فشل'); });
}
function issue(){
  api('POST','/api/admin/keys',{label:$('label').value}).then(r=>{
    if(!r.ok){uiAlert(r.error||'فشل');return;}
    $('secret').classList.remove('hidden');
    $('secret').textContent='مفتاحك الجديد (يُعرض مرة واحدة): '+r.api_key;
    $('label').value='';
    load();
  });
}
window.onload=()=>{ $('adminkey').value=localStorage.getItem('aali_admin_token')||''; if($('adminkey').value) load(); };
</script>
</body>
</html>"""
    return html


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DIST_DIR = WEB_DIR / "dist"


@app.route("/ui/", defaults={"filename": "index.html"})
@app.route("/ui/<path:filename>")
def ui_client(filename: str):
    """Serve the built desktop app (web/dist) when it exists, falling back to
    the raw web/ directory for development."""
    if DIST_DIR.is_dir() and (DIST_DIR / filename).is_file():
        response = send_from_directory(DIST_DIR, filename)
    elif DIST_DIR.is_dir() and filename == "index.html" and (DIST_DIR / "index.html").is_file():
        response = send_from_directory(DIST_DIR, "index.html")
    else:
        response = send_from_directory(WEB_DIR, filename)
    if filename == "index.html":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/signup")
def signup_page():
    """User-facing self-serve signup: issues an Aali key and drops the visitor
    straight into the chat UI (web/signup.html; stores the key in localStorage)."""
    response = send_from_directory(WEB_DIR, "signup.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def openai_compat():
    """OpenAI-compatible chat endpoint so opencode / Cursor / Aider / Zed etc.
    can use Aali as their model: point the tool at
      base_url = http://<pc-ip>:5055/v1   (api_key: a real Aali-issued key)
    Runs the full agent loop with tools; returns an OpenAI-shaped response.
    """
    if request.method == "OPTIONS":
        return make_response(("", 204))
    # Aali is the provider: this public endpoint accepts ONLY real keys —
    # the admin master key or an issued per-user key — never "anything".
    bearer = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:].strip()
    presented = bearer or _client_key()
    if API_KEY:
        auth = _auth_state(presented) if presented else None
        if not auth:
            return make_response({"error": {"message": "invalid API key", "type": "auth_error", "code": "invalid_api_key"}}, 401)
        if not auth["is_admin"] and apikeys.over_daily_cap(auth["key_id"]):
            return make_response({"error": {"message": "daily request cap reached", "type": "quota_error", "code": "rate_limit"}}, 429)
        apikeys.record_usage(auth["key_id"])
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
    _meter_chars(len(message), len(reply))
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


def _server_port() -> int:
    """Resolve the listen port. Defends against harnesses that inject PORT=0
    ("pick any free port" for their own tooling) into the process environment:
    port 0 makes Flask bind an ephemeral port, which silently breaks every
    client that expects Aali on the documented port. Anything invalid or <= 0
    falls back to the documented default (5055; the run doc's health check)."""
    raw = (os.getenv("PORT") or "").strip()
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if port <= 0:
        port = int(os.getenv("AALI_DEFAULT_PORT", "5055"))
    return port


if __name__ == "__main__":
    _ensure_sessions_loaded()
    _port = _server_port()
    print(f"Aali chat UI starting on http://127.0.0.1:{_port}")
    app.run(host="0.0.0.0", port=_port, debug=False)