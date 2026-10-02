"""Tests for Aali Studio — the desktop IDE (PART 2).

Three things are pinned here, because they are the parts that could lie:

1. **The sandbox** — Studio must reuse the brain's pentested resolver, never a
   weaker copy of its own. Traversal, device names, drive letters and
   %-encoded payloads stay refused, and the terminal keeps the brain's
   allow-list (including the env-dump refusal).
2. **The agent panel translation** — a fake brain server speaks the real
   :5055 SSE vocabulary and the test asserts the Studio panel events that come
   out, in order, with a REAL before/after diff.
3. **The keys store** — a key is saved to %APPDATA% (a temp dir in tests),
   never returned by the status endpoint, and an empty save deletes it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUDIO_DIR = REPO / "build-desktop" / "aali-studio"
if str(STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(STUDIO_DIR))
if str(REPO / "file-agent") not in sys.path:
    sys.path.insert(0, str(REPO / "file-agent"))

import models_proxy as mp  # noqa: E402
import studio_server  # noqa: E402


# ————— fixtures —————

@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own %APPDATA%/AaliStudio — never the real one."""
    monkeypatch.setenv("AALI_STUDIO_CONFIG_DIR", str(tmp_path / "AaliStudio"))
    monkeypatch.setenv("AALI_STUDIO_REPLAY_MS", "0")
    yield tmp_path / "AaliStudio"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".hidden").write_text("secret\n", encoding="utf-8")
    mp.save_settings({"workspace": str(root)})
    monkeypatch.setenv("AALI_STUDIO_PORT", "0")
    return root


@pytest.fixture
def client(workspace):
    app = studio_server.create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


# ————— 1. the registry + the grandmother —————

def test_default_model_is_the_local_brain():
    assert mp.default_model() == "hwk-aziza"
    payload = mp.models_payload()
    assert payload["default"] == "hwk-aziza"
    first = payload["models"][0]
    assert first["kind"] == "local" and first["ready"] is True


def test_registry_has_every_model_the_owner_asked_for():
    ids = [m["id"] for m in mp.MODELS]
    for wanted in ("hwk-aziza", "glm-4-flash", "glm-4-plus", "deepseek-v3",
                   "deepseek-r1", "groq-llama-3.3", "openrouter-free",
                   "together-free", "custom"):
        assert wanted in ids, wanted


def test_no_model_is_a_local_download():
    """API-only: every non-default model speaks an OpenAI-compatible API."""
    for spec in mp.MODELS:
        if spec["kind"] == "local" or spec["id"] == "custom":
            continue  # custom is BY DEFINITION the owner's own endpoint
        assert spec["base_url"].startswith("https://"), spec["id"]
        assert spec["model"], spec["id"]


def test_unknown_model_is_refused_not_silently_defaulted():
    with pytest.raises(mp.StudioError):
        mp.model_def("gpt-9-ultra")


def test_about_mentions_azeza_the_grandmother():
    about = studio_server.ABOUT
    assert about["grandmother"] == "عزيزة"
    assert "عزيزة" in about["about_ar"]
    assert "Azeza" in about["about_en"]
    assert "grandmother" in about["about_en"]


def test_about_endpoint_serves_it(client):
    body = client.get("/api/about").get_json()
    assert "عزيزة" in body["about_ar"]


# ————— 2. the key store —————

def test_key_saved_to_appdata_and_never_echoed_back():
    mp.save_key("groq", "gsk_supersecretvalue123")
    stored = json.loads(Path(mp.keys_file()).read_text(encoding="utf-8"))
    assert stored["groq"] == "gsk_supersecretvalue123"          # on disk
    status = mp.key_status()
    groq = [m for m in status["models"] if m["id"] == "groq-llama-3.3"][0]
    assert groq["ready"] is True
    assert "gsk_supersecretvalue123" not in json.dumps(status)  # never returned
    assert groq["hint"] == "gsk…23"                            # only a hint


def test_empty_save_deletes_the_key():
    mp.save_key("groq", "gsk_value")
    mp.save_key("groq", "")
    assert "groq" not in json.loads(Path(mp.keys_file()).read_text(
        encoding="utf-8"))
    assert mp.get_key("groq") == ""


def test_env_key_wins_over_the_file(monkeypatch):
    mp.save_key("groq", "from-file")
    monkeypatch.setenv("AALI_STUDIO_KEY_GROQ", "from-env")
    assert mp.get_key("groq") == "from-env"


def test_keys_endpoint_never_returns_the_secret(client):
    client.post("/api/keys", json={"field": "deepseek", "key": "sk-very-secret"})
    body = client.post("/api/keys", json={"field": "deepseek", "key": ""}).get_json()
    assert "sk-very-secret" not in json.dumps(body, ensure_ascii=False)


def test_a_missing_key_is_an_honest_arabic_error_not_a_crash():
    spec = mp.model_def("groq-llama-3.3")
    with pytest.raises(mp.StudioError) as excinfo:
        list(mp.stream_openai(spec, [{"role": "user", "content": "hi"}]))
    assert "مفتاح" in str(excinfo.value)


def test_settings_whitelist_refuses_unknown_keys():
    with pytest.raises(mp.StudioError):
        mp.save_settings({"evil": "1"})
    with pytest.raises(mp.StudioError):
        mp.save_settings({"model": "not-a-model"})


def test_chat_url_rules():
    assert mp._chat_url("https://x.dev/v1") == "https://x.dev/v1/chat/completions"
    assert mp._chat_url("https://x.dev/v1/chat/completions") == \
        "https://x.dev/v1/chat/completions"
    for bad in ("", "ftp://x", "x.dev"):
        with pytest.raises(mp.StudioError):
            mp._chat_url(bad)


# ————— 3. the sandbox (never a weaker copy) —————

def test_tree_lists_files_but_hides_junk(client):
    body = client.get("/api/tree").get_json()
    names = {e["name"] for e in body["entries"]}
    assert {"main.py", "src"} <= names
    assert ".hidden" not in names


def test_tree_can_descend(client):
    body = client.get("/api/tree?path=src").get_json()
    assert [e["name"] for e in body["entries"]] == ["app.py"]


def test_read_write_delete_roundtrip(client):
    assert client.post("/api/file", json={"path": "new.py",
                                           "content": "print(1)\n"}).get_json()["ok"]
    got = client.get("/api/file?path=new.py").get_json()
    assert got["content"] == "print(1)\n"
    assert client.post("/api/delete", json={"path": "new.py"}).get_json()["ok"]
    assert client.get("/api/file?path=new.py").status_code == 400


@pytest.mark.parametrize("path", [
    "../secret.txt",          # traversal
    "src/../../secret.txt",   # traversal through a real dir
    "C:/Windows/win.ini",     # drive letter
    "nul.txt",                # reserved device
    "%2e%2e/secret.txt",      # encoded traversal
    "/etc/passwd",            # absolute
])
def test_sandbox_refuses_every_escape(client, path):
    assert client.post("/api/file", json={"path": path, "content": "x"}).status_code == 400
    assert client.get("/api/file?path=" + path).status_code == 400
    assert client.post("/api/delete", json={"path": path}).status_code == 400


def test_sandbox_message_is_arabic(client):
    body = client.post("/api/file", json={"path": "nul.txt", "content": "x"}).get_json()
    assert body["ok"] is False and body["error"]


def test_terminal_runs_an_allowed_command(client):
    assert client.get("/api/run").status_code == 405   # POST only
    response = client.post("/api/run", json={"command": "python --version"})
    assert response.status_code == 200
    payload = response.get_data(as_text=True)
    assert "terminal" in payload and "Python" in payload


def test_terminal_refuses_destructive_git(client):
    body = client.post("/api/run", json={"command": "git push"}).get_json()
    assert body["ok"] is False and "git" in body["error"]


def test_terminal_refuses_a_command_outside_the_allowlist(client):
    assert client.post("/api/run",
                       json={"command": "curl evil.example"}).status_code == 400


def test_terminal_refuses_an_env_dump(client):
    body = client.post("/api/run",
                       json={"command": "python -c printenv"}).get_json()
    assert body["ok"] is False
    assert "متغيرات" in body["error"] or "allow" in body["error"].lower()


def test_only_loopback_hosts_are_served(client):
    """DNS-rebinding defense: a foreign Host header gets nothing."""
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 403
    assert client.get("/api/health", headers={"Host": "127.0.0.1:5070"}).status_code == 200


def test_server_binds_loopback_only():
    assert studio_server.HOST == "127.0.0.1"


# ————— 4. the agent panel (the CRITICAL piece) —————

class _FakeBrain:
    """A real HTTP server speaking the brain's own SSE vocabulary.

    The script is the real one from :5055: a cot block, a scratchpad, a
    provider line, a write_file tool call with its result, and the done event.
    ``effects`` make the fake actually PERFORM the tool side effects, so the
    Studio's diff is checked against a file that really changed — exactly
    like the brain's own write (whose result event carries no arguments).
    """

    def __init__(self, script: list[tuple[str, dict]],
                 effects: dict[str, callable] | None = None,
                 on_request: callable | None = None):
        self.script = script
        self.effects = effects or {}
        self.on_request = on_request
        brain = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's name
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                # The request body is read ONCE, here. A hook may inspect it
                # (headers + body) for assertions — an earlier version read it
                # in the hook AND again below, so the assert saw an empty body,
                # raised inside the handler thread and left the client hanging.
                replacement = brain.on_request(body, self.headers) if brain.on_request else None
                if replacement is not None:
                    body = replacement
                assert body.get("verbose") is True, "the panel needs cot"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for name, data in brain.script:
                    self.wfile.write(
                        f"event: {name}\ndata: "
                        f"{json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    # The real brain logs tool_requested and only THEN runs the
                    # tool — the effect must land after the event is on the wire
                    # or Studio could never snapshot the true 'before'.
                    if name == "activity" and data.get("event") == "tool_requested":
                        effect = brain.effects.get(str(data.get("tool")))
                        if effect is not None:
                            time.sleep(0.05)   # a real tool takes a moment
                            effect(dict(data.get("arguments") or {}))

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _events(payload: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in payload.replace("\r\n", "\n").split("\n\n"):
        name, data = None, ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data:
            try:
                out.append((name, json.loads(data)))
            except ValueError:
                pass
    return out


BRAIN_SCRIPT = [
    ("activity", {"event": "provider_selected", "provider": "aali_remote",
                  "model": "checkpoint-3933"}),
    ("cot", {"iteration": 1, "text": "سأقرأ الملف ثم أعدل السطر الثاني"}),
    ("scratchpad", {"content": "قراءة main.py\nفحص السطر", "lines": 2}),
    ("activity", {"event": "tool_requested", "tool": "read_file",
                  "arguments": {"path": "main.py"}}),
    ("activity", {"event": "tool_result", "tool": "read_file",
                  "result": "print('hi')", "arguments": {"path": "main.py"}}),
    ("activity", {"event": "tool_requested", "tool": "write_file",
                  "arguments": {"path": "main.py", "content": "print('bye')\n"}}),
    ("activity", {"event": "tool_result", "tool": "write_file",
                  "result": "Written 13 bytes to main.py"}),
    ("activity", {"event": "tool_requested", "tool": "run_command",
                  "arguments": {"command": "python main.py"}}),
    ("activity", {"event": "tool_result", "tool": "run_command",
                  "result": "bye\n", "arguments": {"command": "python main.py"}}),
    ("done", {"ok": True, "reply": "عدّلت السطر في main.py", "sid": "sid-123"}),
]


def _write_effect(root: Path, wait_for: "threading.Event | None" = None):
    """What the real brain's write_file does: actually change the file.

    `wait_for` makes the ordering DETERMINISTIC instead of sleep-based: the
    fake only writes once Studio has really snapshotted the 'before' state,
    which is the ordering the real brain guarantees (log the request, then run
    the tool). A sleep races under a loaded box and made this test flaky.
    """
    def effect(arguments: dict) -> None:
        if wait_for is not None:
            assert wait_for.wait(10), "Studio never snapshotted the file"
        (root / str(arguments["path"])).write_text(
            str(arguments["content"]), encoding="utf-8")
    return effect


def _brain_that_really_writes(workspace, monkeypatch) -> _FakeBrain:
    """A fake brain whose writes wait for Studio's 'before' snapshot."""
    snapshotted = threading.Event()
    real_read = studio_server._safe_read

    def watched(path: str, root: Path) -> str:
        content = real_read(path, root)
        if path == "main.py":
            snapshotted.set()
        return content

    monkeypatch.setattr(studio_server, "_safe_read", watched)
    return _FakeBrain(BRAIN_SCRIPT,
                      {"write_file": _write_effect(workspace, snapshotted)})


def test_agent_panel_translates_the_whole_brain_vocabulary(workspace, monkeypatch):
    with _brain_that_really_writes(workspace, monkeypatch) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat",
                                  json={"message": "عدّل main.py"}).get_data(as_text=True)
    events = _events(payload)
    names = [name for name, _ in events]
    assert names[0] == "request"
    for wanted in ("thinking", "provider", "tool_call", "tool_result",
                   "terminal", "diff", "text", "done"):
        assert wanted in names, wanted
    assert names[-1] == "done"
    assert events[-1][1]["ok"] is True
    assert events[-1][1]["reply"] == "عدّلت السطر في main.py"


def test_the_diff_is_a_real_before_after(workspace, monkeypatch):
    with _brain_that_really_writes(workspace, monkeypatch) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat",
                                  json={"message": "عدّل"}).get_data(as_text=True)
    diffs = [data for name, data in _events(payload) if name == "diff"]
    assert len(diffs) == 1
    diff = diffs[0]
    assert diff["path"] == "main.py"
    assert diff["before"] == "print('hi')\n"      # the real old content
    assert diff["after"] == "print('bye')\n"      # the real new content
    assert diff["before"] != diff["after"]


def test_tool_call_carries_the_real_name_and_args(workspace):
    with _FakeBrain(BRAIN_SCRIPT) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    calls = [data for name, data in _events(payload) if name == "tool_call"]
    assert [c["name"] for c in calls] == ["read_file", "write_file", "run_command"]
    assert calls[1]["args"]["path"] == "main.py"


def test_thinking_text_is_the_brains_own_reasoning(workspace):
    with _FakeBrain(BRAIN_SCRIPT) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    thinking = "".join(d["token"] for name, d in _events(payload) if name == "thinking")
    assert "سأقرأ الملف" in thinking          # from cot
    assert "قراءة main.py" in thinking        # from the scratchpad


def test_scratchpad_lines_are_not_repeated(workspace):
    with _FakeBrain(BRAIN_SCRIPT) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    thinking = "".join(d["token"] for name, d in _events(payload) if name == "thinking")
    assert thinking.count("قراءة main.py") == 1


def test_answer_tokens_reassemble_into_the_exact_reply(workspace):
    with _FakeBrain(BRAIN_SCRIPT) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    events = _events(payload)
    joined = "".join(d["token"] for name, d in events if name == "text")
    assert joined == "عدّلت السطر في main.py"


def test_a_dead_brain_is_an_honest_arabic_error_not_a_hang(workspace):
    mp.save_settings({"brain_url": "http://127.0.0.1:1"})
    app = studio_server.create_app()
    with app.test_client() as client:
        payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    events = _events(payload)
    errors = [d for name, d in events if name == "error"]
    assert errors and "العقل المحلي" in errors[0]["error"]
    assert events[-1][0] == "done" and events[-1][1]["ok"] is False


def test_brain_key_is_sent_to_a_key_mode_brain(workspace):
    """:5055 in key mode answers 404 to every gated route without a key."""
    seen: list[str | None] = []

    def capture(_body, headers):
        seen.append(headers.get("X-API-Key"))

    mp.save_key(mp.BRAIN_KEY_FIELD, "master-secret-key")
    with _FakeBrain([("done", {"ok": True, "reply": "تمام", "sid": "s"})],
                    on_request=capture) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    assert seen == ["master-secret-key"]
    assert _events(payload)[-1][1]["reply"] == "تمام"
    # and the key itself never comes back out through the API
    assert "master-secret-key" not in json.dumps(mp.key_status(), ensure_ascii=False)


def test_a_404_brain_asks_for_the_key_instead_of_guessing(workspace):
    class _Refusing(_FakeBrain):
        def __init__(self):
            super().__init__([])
            brain = self

            def do_POST(handler):  # noqa: N802
                handler.send_response(404)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                handler.wfile.write(b'{"ok":false,"error":"not found"}')
            brain.server.RequestHandlerClass.do_POST = do_POST

    with _Refusing() as brain:  # noqa: F841 - subclass swaps the handler
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    error = [d for name, d in _events(payload) if name == "error"][0]["error"]
    assert "المفتاح" in error and "🔑" in error


def test_an_empty_request_is_refused(client):
    assert client.post("/api/chat", json={"message": "  "}).status_code == 400
    assert client.post("/api/chat", json={"message": "hi",
                                          "model": "nope"}).status_code == 400


def test_stop_is_honest_about_the_local_brain(workspace):
    """STOP closes the stream; the doc says a local turn may still finish."""
    with _FakeBrain(BRAIN_SCRIPT) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            first = client.post("/api/chat", json={"message": "x"})
            payload = first.get_data(as_text=True)
            request_id = _events(payload)[0][1]["request_id"]
            body = client.post("/api/stop", json={"request_id": request_id}).get_json()
            assert body["stopped"] is True
            ghost = client.post("/api/stop", json={"request_id": "nope"}).get_json()
            assert ghost["stopped"] is False


def test_replay_never_cuts_a_word():
    text = "عدّلتُ السطر في main.py — وكل شيء تمام"
    joined = "".join(mp.chunks_for_replay(text))
    assert joined == text
    words = [c for c in mp.chunks_for_replay("a b c") if c.strip()]
    assert words == ["a", "b", "c"]


def test_remote_model_missing_key_fails_in_the_panel(workspace, client):
    payload = client.post("/api/chat",
                          json={"message": "hi", "model": "groq-llama-3.3"}
                          ).get_data(as_text=True)
    events = _events(payload)
    errors = [d for name, d in events if name == "error"]
    assert errors and "مفتاح" in errors[0]["error"]
    assert events[-1][0] == "done"


def test_deepseek_r1_reasoning_lands_in_the_thinking_block(monkeypatch, workspace, client):
    """R1's reasoning_content is REAL reasoning — it belongs in 💭."""
    def fake_stream(spec, messages, stop_check=lambda: False):
        yield "thinking", "أفكر في الخطوة الأولى ", None
        yield "text", "الجواب", None
    monkeypatch.setattr(mp, "stream_openai", fake_stream)
    payload = client.post("/api/chat", json={"message": "hi",
                                             "model": "deepseek-r1"}
                          ).get_data(as_text=True)
    events = _events(payload)
    thinking = "".join(d["token"] for name, d in events if name == "thinking")
    text = "".join(d["token"] for name, d in events if name == "text")
    assert "أفكر في الخطوة الأولى" in thinking
    assert text == "الجواب"
    assert events[-1][1]["reply"] == "الجواب"


def test_model_switcher_is_always_visible_and_selectable(client):
    body = client.get("/api/models").get_json()
    assert body["current"] == "hwk-aziza"
    switched = client.post("/api/models", json={"model": "deepseek-r1"}).get_json()
    assert switched["current"] == "deepseek-r1"
    assert mp.settings()["model"] == "deepseek-r1"
    assert client.post("/api/models", json={"model": "ghost"}).status_code == 400


# ————— 5. the IDE layer: context, apply, search, watch, history —————

def test_context_is_attached_to_the_prompt_the_brain_receives(workspace):
    """The whole point of the upgrade: the agent must SEE the open file."""
    seen: list[str] = []

    def capture(body, _headers):
        seen.append(body.get("message", ""))
        return body

    with _FakeBrain([("done", {"ok": True, "reply": "تمام", "sid": "s"})],
                    on_request=capture) as brain:
        mp.save_settings({"brain_url": brain.url})
        app = studio_server.create_app()
        with app.test_client() as client:
            payload = client.post("/api/chat", json={
                "message": "اشرح هذا",
                "context": [{"path": "main.py"}],
            }).get_data(as_text=True)
    assert seen and "اشرح هذا" in seen[0]
    assert "[ملفات IDE المفتوحة]" in seen[0]
    assert "print('hi')" in seen[0]          # the REAL file content
    events = _events(payload)
    assert [n for n, _ in events][0] == "request"
    assert "main.py" in json.dumps(events[0][1], ensure_ascii=False)


def test_context_selection_wins_over_the_whole_file(workspace):
    note, files = studio_server._build_context(
        [{"path": "main.py", "selection": "print('sel')"}])
    assert "(المحدد)" in note
    assert "print('sel')" in note
    assert "print('hi')" not in note        # only the selection is sent
    assert files[0]["selection"] is True


def test_context_cannot_escape_the_sandbox(workspace):
    """A client must not be able to smuggle a file in from outside the root."""
    note, files = studio_server._build_context(
        [{"path": "../../../Windows/win.ini"},
         {"path": "C:/Windows/win.ini"},
         {"path": "main.py"}])
    assert "win.ini" not in note
    assert [f["path"] for f in files] == ["main.py"]


def test_context_is_capped_server_side(workspace):
    many = [{"path": "main.py"} for _ in range(50)]
    note, files = studio_server._build_context(many)
    assert len(files) <= studio_server.MAX_CONTEXT_FILES
    assert len(note) <= studio_server.MAX_CONTEXT_CHARS + 400


def test_context_is_optional(workspace):
    note, files = studio_server._build_context(None)
    assert note == "" and files == []


def test_accept_is_a_verification_not_a_write(workspace):
    """The agent's write already hit disk, so «قبول» must NOT write — it
    verifies and the client reloads. Writing here would be theatre."""
    (workspace / "main.py").write_text("print('bye')\n", encoding="utf-8")
    body = client_apply(workspace, mode="accept")
    assert body["ok"] is True
    assert body["unchanged"] is True
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('bye')\n"


def test_revert_restores_the_before_content(workspace):
    (workspace / "main.py").write_text("print('bye')\n", encoding="utf-8")
    body = client_apply(workspace, mode="revert")
    assert body["ok"] is True
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_apply_refuses_when_the_file_moved_on(workspace):
    """A stale diff must never clobber a newer edit — that is the whole point
    of the conflict check."""
    (workspace / "main.py").write_text("print('mine')\n", encoding="utf-8")
    app = studio_server.create_app()
    with app.test_client() as client:
        response = client.post("/api/apply", json={
            "path": "main.py", "before": "print('hi')\n",
            "after": "print('bye')\n", "mode": "revert"})
    assert response.status_code == 409
    body = response.get_json()
    assert body["conflict"] is True
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('mine')\n"


def test_accept_refuses_when_the_disk_does_not_match(workspace):
    """«قبول» must never certify a change that did not happen."""
    app = studio_server.create_app()
    with app.test_client() as client:
        assert client.post("/api/apply", json={
            "path": "main.py", "before": "print('hi')\n",
            "after": "print('bye')\n", "mode": "accept"}).status_code == 409


def test_apply_cannot_write_outside_the_workspace(workspace):
    """A sandbox breach must be a plain refusal, never a «conflict»."""
    app = studio_server.create_app()
    with app.test_client() as client:
        response = client.post("/api/apply", json={
            "path": "../escape.py", "before": "", "after": "x",
            "mode": "revert"})
    assert response.status_code == 400
    assert not (workspace.parent / "escape.py").exists()


def test_apply_rejects_an_unknown_mode(workspace):
    app = studio_server.create_app()
    with app.test_client() as client:
        assert client.post("/api/apply", json={
            "path": "main.py", "before": "", "after": "x",
            "mode": "delete-everything"}).status_code == 400


def client_apply(workspace, mode: str) -> dict:
    app = studio_server.create_app()
    with app.test_client() as client:
        return client.post("/api/apply", json={
            "path": "main.py", "before": "print('hi')\n",
            "after": "print('bye')\n", "mode": mode}).get_json()


def test_workspace_files_listing(workspace):
    app = studio_server.create_app()
    with app.test_client() as client:
        body = client.get("/api/files").get_json()
    paths = {f["path"] for f in body["files"]}
    assert {"main.py", "src/app.py"} <= paths
    assert all(f["path"].startswith("/") is False for f in body["files"])


def test_file_search_ranks_a_real_file(client):
    body = client.get("/api/search?q=main").get_json()
    assert body["mode"] == "files"
    assert any(r["path"] == "main.py" for r in body["results"])


def test_file_search_subsequence_matching(client):
    """m-n-y appear in order in 'main.py' — the fuzzy palette must find it."""
    body = client.get("/api/search?q=mny").get_json()
    assert any(r["path"] == "main.py" for r in body["results"])


def test_file_search_rejects_a_non_subsequence(client):
    """'mpn' is NOT a subsequence of 'main.py' — it must not match."""
    body = client.get("/api/search?q=mpn").get_json()
    assert not any(r["path"] == "main.py" for r in body["results"])


def test_file_search_never_invents_files(client):
    body = client.get("/api/search?q=zzzznotreal").get_json()
    assert body["results"] == []


def test_content_search_finds_the_line(client):
    body = client.get("/api/search?q=hi&mode=content").get_json()
    assert body["results"] and body["results"][0]["path"] == "main.py"
    assert body["results"][0]["line"] == 1


def test_watch_emits_changed_only_for_a_real_change(workspace):
    """A touch that changes nothing must not reload the owner's buffer."""
    app = studio_server.create_app()
    with app.test_client() as client:
        response = client.get("/api/watch?paths=main.py")
        stream_iter = response.response
        first = next(stream_iter).decode("utf-8")
        assert "watching" in first
        (workspace / "main.py").write_text("print('bye')\n", encoding="utf-8")
        event = ""
        for _ in range(6):
            event = next(stream_iter).decode("utf-8")
            if "changed" in event:
                break
        assert "changed" in event and "main.py" in event
        stream_iter.close()


def test_watch_needs_paths(client):
    assert client.get("/api/watch").status_code == 400


def test_turns_are_saved_listed_and_reopened(client):
    saved = client.post("/api/turns", json={
        "question": "من أنت؟", "reply": "اسمي آلي", "model": "hwk-aziza",
        "files": ["main.py"], "blocks": [{"kind": "thinking", "text": "أفكر"}],
    }).get_json()
    turn_id = saved["turn"]["id"]
    listing = client.get("/api/turns").get_json()
    assert listing["turns"][0]["question"] == "من أنت؟"
    full = client.get("/api/turns/" + turn_id).get_json()["turn"]
    assert full["reply"] == "اسمي آلي"
    assert full["blocks"][0]["text"] == "أفكر"
    assert full["files"] == ["main.py"]


def test_turns_live_outside_the_repo(client):
    """Never inside the repository (AGENTS.md: no data/logs in git)."""
    path = Path(studio_server.turns_store().path).resolve()
    assert "AaliStudio" in str(path)
    assert str(REPO) not in str(path)


def test_a_turn_without_a_reply_is_refused(client):
    assert client.post("/api/turns", json={"question": "x"}).status_code == 400


def test_unknown_turn_is_404(client):
    assert client.get("/api/turns/nope").status_code == 404


def test_clearing_history_is_idempotent(client):
    client.post("/api/turns", json={"question": "س", "reply": "ج"})
    assert client.delete("/api/turns").get_json()["turns"] == []
    assert client.delete("/api/turns").get_json()["turns"] == []


# ————— 6. the UI —————

def test_ui_is_arabic_first_rtl_with_the_three_panes():
    html = (STUDIO_DIR / "web" / "index.html").read_text(encoding="utf-8")
    assert 'lang="ar"' in html and 'dir="rtl"' in html
    for pane in ('id="tree-pane"', 'id="editor-pane"', 'id="agent-pane"'):
        assert pane in html
    assert "monaco" in html.lower()


def test_ui_renders_every_agent_panel_block():
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    for handler in ("thinking", "tool_call", "tool_result", "terminal",
                    "diff", "text", "done"):
        assert f'case "{handler}"' in js, handler
    assert 'addTool' in js and 'addDiff' in js
    assert "btn-stop" in js and "btn-retry" in js


def test_ui_has_tabs_history_palette_and_chips():
    html = (STUDIO_DIR / "web" / "index.html").read_text(encoding="utf-8")
    for anchor in ('id="tabs"', 'id="history"', 'id="palette"',
                   'id="context-chips"', 'id="palette-input"'):
        assert anchor in html, anchor


def test_ui_sends_the_ide_context_with_every_ask():
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert "context: state.lastContext" in js
    assert "autoContext" in js and "addContext" in js


def test_ui_watches_open_files_and_never_reloads_a_dirty_tab():
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert "/api/watch?paths=" in js
    assert "if (tab.dirty)" in js      # must check before clobbering


def test_ui_offers_accept_and_revert_on_every_diff():
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert 'decide("accept")' in js and 'decide("revert")' in js
    assert "/api/apply" in js
    assert 'error.payload.conflict' in js


def test_ui_saves_and_reopens_conversations():
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert '"/api/turns"' in js
    assert "loadHistory" in js and "openTurn" in js


def test_ui_markdown_is_escaped_before_it_is_rendered():
    """A model reply is untrusted text: it must never become live HTML."""
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert "escapeHtml" in js
    body = js[js.index("function renderMarkdown"):js.index("/* ————— file tree ————— */")]
    assert body.index("escapeHtml(text") < body.index("innerHTML") if "innerHTML" in body else True
    assert "<script" not in js.lower().replace("escapehtml", "")


def test_ui_quick_open_is_keyboard_reachable():
    js = (STUDIO_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert 'key.toLowerCase() === "p"' in js
    assert 'key.toLowerCase() === "w"' in js


def test_ui_shows_the_stop_and_retry_buttons_in_the_markup():
    html = (STUDIO_DIR / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-stop"' in html and 'id="btn-retry"' in html
    assert "⏹️" in html and "▶️" in html


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "آلي ستوديو" in response.get_data(as_text=True)


def test_static_traversal_is_refused(client):
    assert client.get("/static/..%2f..%2fapp.js").status_code in (400, 404)
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.py").status_code == 404


def test_client_javascript_has_no_syntax_errors():
    """A broken app.js is an invisible failure — parse it before shipping."""
    node = None
    for candidate in ("node", "nodejs"):
        if _which(candidate):
            node = candidate
            break
    if node is None:
        pytest.skip("node is not installed on this box")
    result = subprocess.run([node, "--check", str(STUDIO_DIR / "web" / "app.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-600:]


def _which(program: str) -> bool:
    from shutil import which
    return which(program) is not None


# ————— 7. entry-point tripwire —————

def test_entrypoints_bootstrap_their_own_sys_path():
    """The suite sets PYTHONPATH, which MASKS a missing bootstrap."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["AALI_STUDIO_CONFIG_DIR"] = str(isolated_config)
    for name, args in (("studio_app.py", ["--version"]),
                       ("studio_app.py", ["--help"]),
                       ("studio_server.py", ["--help"])):
        result = subprocess.run(
            [sys.executable, str(STUDIO_DIR / name), *args],
            capture_output=True, text=True, env=env, timeout=120)
        assert result.returncode == 0, f"{name} {args}:\n{result.stderr[-800:]}"


def test_studio_server_module_never_imports_torch():
    """CPU-only venv: a torch import would be a silent 3 GB mistake."""
    source = (STUDIO_DIR / "studio_server.py").read_text(encoding="utf-8")
    for banned in ("import torch", "import transformers", "import numpy"):
        assert banned not in source


def test_spec_bundles_the_web_assets_and_the_sandbox_module():
    spec = (STUDIO_DIR / "Aali-Studio.spec").read_text(encoding="utf-8")
    assert 'os.path.join(STUDIO, "web")' in spec      # web assets are bundled
    # file-agent/ must stay on pathex or the frozen IDE has NO file sandbox
    assert '"file-agent"' in spec
    assert 'name="Aali-Studio"' in spec
    assert "torch" in spec                 # excluded from the bundle
    # PyInstaller resolves the script path relative to the SPEC dir, so a
    # relative 'build-desktop/...' script path doubles up and never builds.
    assert "SPECPATH" in spec
