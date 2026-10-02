"""آلي ريتش — Aali Reach: the fence is the feature.

A link reader that sits next to ``run_command`` can read the owner's LAN, reach
the cloud metadata address, or be used as an exfiltration channel. So most of
this file is about what it REFUSES, not what it reads.

No test here touches the network, except through a real loopback HTTP server
whose host the fence is monkeypatched to allow — that is how the redirect
re-guard is proven (a 302 into a private address must still be refused).
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "file-agent"))
sys.path.insert(0, str(REPO / "scripts"))

from file_agent import file_tools, reach  # noqa: E402


# ————— 1. the URL fence —————

@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "data:text/html,<h1>x",
    "gopher://example.com",
    "",
    "   ",
    "not-a-url",
    "http://",
    "http://user:secret@example.com/",
    "http://example.com/a b",
    "http://localhost:5055/api/ask",
    "http://LOCALHOST/x",
    "http://box.local/x",
    "x" * 2500,
])
def test_the_fence_refuses_every_non_public_shape(url):
    with pytest.raises(reach.ReachError):
        reach.validate_url(url)


@pytest.mark.parametrize("url,expected", [
    ("https://example.com", "https://example.com/"),
    ("HTTP://Example.COM/Path", "http://example.com/Path"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com/?a=1#frag", "https://example.com/?a=1"),
])
def test_valid_urls_are_normalized_not_mangled(url, expected):
    """A fragment is dropped (it never reaches the server); the rest survives."""
    assert reach.validate_url(url) == expected


@pytest.mark.parametrize("host,why", [
    ("127.0.0.1", "loopback"),
    ("127.1.2.3", "loopback"),
    ("10.0.0.5", "RFC1918"),
    ("172.16.4.4", "RFC1918"),
    ("192.168.1.13", "RFC1918"),
    ("169.254.169.254", "link-local"),
    ("0.0.0.0", "unspecified"),
    ("224.0.0.1", "multicast"),
    ("::1", "loopback"),
    ("[::1]", "loopback"),
    ("100.64.0.1", "CGNAT"),
])
def test_every_internal_address_is_refused_by_name(host, why):
    ok, reason = reach._is_public_ip(host)
    assert ok is False
    # The refusal must NAME the right thing: calling the cloud metadata
    # address "RFC1918" is a wrong label on the exact IP that leaks keys.
    assert why.split()[0].lower() in reason.lower(), (host, reason)


def test_a_public_literal_address_is_allowed_but_a_private_one_is_not():
    assert reach.guard_host("93.184.216.34", 443) == ["93.184.216.34"]
    with pytest.raises(reach.ReachError):
        reach.guard_host("192.168.1.13", 5055)


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    """DNS rebinding bait: a public-looking name with a private answer."""
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(reach.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(reach.ReachError) as excinfo:
        reach.resolve_public_ips("totally-public.example", 80)
    assert "loopback" in str(excinfo.value)


def test_a_name_with_one_private_answer_among_public_ones_is_refused(monkeypatch):
    """One bad record poisons the name: the next lookup can be the other one."""
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]

    monkeypatch.setattr(reach.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(reach.ReachError):
        reach.resolve_public_ips("mixed.example", 80)


def test_an_unresolvable_host_is_an_honest_error_not_a_silent_empty(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(reach.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(reach.ReachError) as excinfo:
        reach.resolve_public_ips("nope.example", 80)
    assert "cannot resolve" in str(excinfo.value)


def test_urqlib_redirects_are_disabled_so_we_can_guard_every_hop():
    """urllib follows a 302 BEFORE the caller can see the new host. That is the
    whole attack, so the handler must refuse to follow anything."""
    assert reach._NoRedirect().redirect_request(None, None, 302, "Found",
                                              {}, "http://127.0.0.1") is None


# ————— 2. the extractor —————

SAMPLE = """<!doctype html>
<html><head>
  <title>  Ignored  </title>
  <meta property="og:title" content="The real title">
  <meta name="author" content="Hiba">
  <meta property="article:published_time" content="2026-10-01T09:00:00Z">
  <meta name="description" content="A short description">
  <script>var secret = "should never appear";</script>
  <style>.x{color:red}</style>
</head>
<body>
  <h1>The real title</h1>
  <p>First paragraph of the post.</p>
  <p>Second one with an <a href="https://example.org/other">outbound link</a>.</p>
  <img src="https://example.org/a.png" alt="a cat">
  <noscript>enable js</noscript>
</body></html>"""


def _fetched(body: str, content_type: str = "text/html; charset=utf-8"):
    return reach.FetchResult(url="https://example.com/p", final_url="https://example.com/p",
                             status=200, body=body.encode("utf-8"),
                             content_type=content_type, elapsed_ms=1)


def test_the_extractor_keeps_the_text_and_drops_the_scripts():
    out = reach.extract(_fetched(SAMPLE))
    assert out["title"] == "The real title"
    assert out["author"] == "Hiba"
    assert out["published"].startswith("2026-10-01")
    assert out["description"] == "A short description"
    text = out["text"]
    assert "First paragraph" in text and "Second one" in text
    assert "secret" not in text, "script bodies must never reach the model"
    assert "color:red" not in text, "stylesheet bodies must never reach the model"
    assert "enable js" not in text
    assert "a cat" in text, "image alt text IS content"
    assert "https://example.org/other" in out["links"]


def test_the_text_cap_is_reported_not_hidden():
    long_page = "<html><body>" + ("word " * 40_000) + "</body></html>"
    out = reach.extract(_fetched(long_page))
    assert len(out["text"]) <= reach.MAX_TEXT_CHARS
    assert "text_capped" in out["caps"]


def test_a_binary_response_is_named_not_pretended_to_be_text():
    out = reach.extract(_fetched("\x89PNG\r\n\x1a\n", "image/png"))
    assert out["text"] == ""
    assert "binary_skipped" in out["caps"]


def test_malformed_html_degrades_with_a_note_instead_of_crashing():
    out = reach.extract(_fetched("<html><p>unclosed <b>bold <div>x"))
    assert out["text"]
    assert isinstance(out["caps"], list)


def test_a_json_response_comes_back_as_json_text():
    out = reach.extract(_fetched('{"a": 1}', "application/json"))
    assert '"a"' in out["text"]


# ————— 3. a real fetch, loopback, fence relaxed for the test host only —————

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence the test output
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/secret")
            self.end_headers()
            return
        if self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
            return
        if self.path == "/huge":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(reach.MAX_BYTES + 10))
            self.end_headers()
            self.wfile.write(b"x" * 100)
            return
        if self.path == "/boom":
            self.send_response(500)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>server exploded</h1>")
            return
        body = SAMPLE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def loopback_server(monkeypatch):
    """A real HTTP server, with the fence relaxed for ITS address only.

    Relaxing it only for the bound port keeps the redirect test honest: a hop to
    a different private address still goes through the real fence and must be
    refused.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_guard = reach.guard_host

    def guard(host, host_port):
        if host in ("127.0.0.1", "localhost") and host_port == port:
            return ["127.0.0.1"]
        return real_guard(host, host_port)

    monkeypatch.setattr(reach, "guard_host", guard)
    monkeypatch.setattr(reach, "AUDIT_PATH", Path(os.environ.get("TEMP", ".")) / "reach-test-audit.jsonl")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_public_page_is_read_end_to_end(loopback_server):
    record = reach.read_link(loopback_server + "/post", engine="static")
    assert record["ok"] is True
    result = record["result"]
    assert result["title"] == "The real title"
    assert result["status"] == 200
    assert "First paragraph" in result["text"]
    assert result["engine"] == "static"
    assert "📄 The real title" in reach.summary(record)


def test_a_redirect_into_a_private_address_is_refused(loopback_server):
    """The 302 -> 127.0.0.1 case. urllib would have followed it silently."""
    with pytest.raises(reach.ReachError) as excinfo:
        reach._open(loopback_server + "/redirect-private", 10)
    assert "loopback" in str(excinfo.value)


def test_a_redirect_loop_stops_at_the_cap(loopback_server):
    with pytest.raises(reach.ReachError) as excinfo:
        reach._open(loopback_server + "/redirect-loop", 10)
    assert "redirect" in str(excinfo.value).lower()


def test_an_oversized_declared_length_is_refused_before_the_body_arrives(loopback_server):
    with pytest.raises(reach.ReachError) as excinfo:
        reach._open(loopback_server + "/huge", 10)
    assert "cap" in str(excinfo.value).lower()


def test_a_server_error_is_reported_honestly(loopback_server):
    with pytest.raises(reach.ReachError) as excinfo:
        reach._open(loopback_server + "/boom", 10)
    assert "500" in str(excinfo.value)


def test_a_failed_read_returns_a_verdict_never_a_fabricated_page(monkeypatch):
    """No browser venv is installed in CI, so `auto` must fail LOUDLY."""
    monkeypatch.setattr(reach, "AUDIT_PATH", Path(os.environ.get("TEMP", ".")) / "reach-test-audit.jsonl")
    record = reach.read_link("https://example.invalid/nothing", engine="static")
    assert record["ok"] is False
    assert record.get("error")
    assert "result" not in record


# ————— 4. the audit is content-free —————

def test_the_audit_records_the_host_and_never_the_page(loopback_server, tmp_path,
                                                       monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(reach, "AUDIT_PATH", audit)
    secret_query = "?access_token=SUPERSECRET&x=1"
    record = reach.read_link(loopback_server + "/post" + secret_query, engine="static")
    assert record["ok"] is True
    line = audit.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["host"] == "127.0.0.1"
    assert entry["ok"] is True
    assert entry["chars"] > 0
    assert entry["text_sha16"]
    blob = line
    assert "SUPERSECRET" not in blob, "a query string can carry a token"
    assert "/post" not in blob, "the path is content too"
    assert "First paragraph" not in blob, "the fetched text must never be audited"


def test_audit_failures_never_break_a_read(monkeypatch):
    monkeypatch.setattr(reach, "AUDIT_PATH", Path("Z:/definitely/not/writable/x.jsonl"))
    record = reach.FetchResult(url="https://example.com/", final_url="https://example.com/",
                               status=200, body=b"", content_type="text/html",
                               elapsed_ms=0)
    reach.audit({"url": "https://example.com/", "ok": True}, {"text": "x"})
    assert record.status == 200


# ————— 5. the browser bridge: read-only, and honest when absent —————

def test_the_bridge_refuses_private_targets_from_inside_the_page():
    """Page JS doing fetch('http://127.0.0.1:5055/api/ask') is THE attack a
    'read-only' reader invites. The route layer must refuse it."""
    import reach_fetch

    assert reach_fetch._fence("http://127.0.0.1:5055/api/ask") is False
    assert reach_fetch._fence("http://169.254.169.254/latest/meta-data") is False
    assert reach_fetch._fence("http://192.168.1.13:5055/") is False
    assert reach_fetch._fence("file:///etc/passwd") is False
    assert reach_fetch._fence("data:text/html,x") is False


def test_the_bridge_only_ever_issues_reads():
    import reach_fetch

    assert reach_fetch.ALLOWED_METHODS == {"GET", "HEAD"}
    source = Path(reach_fetch.__file__).read_text(encoding="utf-8")
    assert "route.abort()" in source
    assert "request.method" in source
    assert "storage_state=None" in source, "a persistent profile is a session"


def test_a_missing_browser_venv_is_an_honest_error_not_a_silent_fallback(monkeypatch):
    monkeypatch.setenv("AALI_REACH_VENV", "Z:/nowhere/reach-venv")
    with pytest.raises(reach.ReachError) as excinfo:
        reach.fetch_rendered("https://example.com")
    message = str(excinfo.value)
    assert "setup_reach.bat" in message, "the refusal must say how to fix it"


def test_the_bridge_is_never_imported_into_the_agent_runtime():
    """AGENTS.md: no heavy deps in the training venv. Playwright must never be
    importable from the agent process - the bridge is a subprocess."""
    source = Path(reach.__file__).read_text(encoding="utf-8")
    assert "import playwright" not in source
    assert "from playwright" not in source
    assert "playwright" not in sys.modules


def test_the_bridge_never_borrows_the_pythonpath():
    """The entry-point tripwire: a borrowed PYTHONPATH would let the bridge
    import the agent's modules (and vice versa)."""
    env = reach.bridge_env()
    assert "PYTHONPATH" not in env
    env.pop("AALI_REACH_VENV", None)


# ————— 6. the tool the agent actually calls —————

def test_a_refusal_is_never_replaced_by_a_downstream_symptom(monkeypatch):
    """The live run caught this: a private address came back as "the reading venv
    is not installed" because `auto` escalated to the browser after the fence
    had already refused. The REAL reason must survive."""
    monkeypatch.setattr(reach, "AUDIT_PATH", Path(os.environ.get("TEMP", ".")) / "reach-test-audit.jsonl")
    for bad, why in (("http://127.0.0.1:5055/api/ask", "loopback"),
                     ("http://169.254.169.254/latest/", "link-local"),
                     ("http://192.168.1.13:5055/", "RFC1918")):
        record = reach.read_link(bad, engine="auto")
        assert record["ok"] is False
        assert why in str(record.get("error")), (bad, record.get("error"))
        assert "venv" not in str(record.get("error")).lower(), (
            "the browser symptom must not hide the fence's refusal")
        # and it must be FAST: a refusal never waits on a browser install
        assert record.get("elapsed_ms", 0) < 5000


def test_read_link_is_registered_with_the_right_shape():
    assert "read_link" in file_tools._FUNCTIONS
    assert "read_link" in file_tools.TOOL_EXECUTION
    names = {d["function"]["name"] for d in file_tools._DEFINITIONS}
    assert "read_link" in names
    definition = next(d for d in file_tools._DEFINITIONS
                      if d["function"]["name"] == "read_link")["function"]
    assert definition["parameters"]["required"] == ["url"]
    # The RIGHT-way sentence is what the 1.5B student learns; without it the
    # toolbelt lesson repeats: the model invents tool names and never calls.
    assert "RIGHT way" in definition["description"] or "read_link" in definition["description"]
    assert "READ-ONLY" in definition["description"]


def test_the_tool_reports_the_fence_not_the_symptom(tmp_path):
    from file_agent.file_tools import FileAgentError

    with pytest.raises(FileAgentError) as excinfo:
        file_tools.read_link("http://192.168.1.13:5055/", tmp_path)
    assert "RFC1918" in str(excinfo.value)
    assert "venv" not in str(excinfo.value).lower()


def test_the_tool_refuses_a_private_address_instead_of_returning_something(tmp_path):
    from file_agent.file_tools import FileAgentError

    with pytest.raises(FileAgentError):
        file_tools.read_link("http://127.0.0.1:5055/api/ask", tmp_path)
    with pytest.raises(FileAgentError):
        file_tools.read_link("http://192.168.1.13:5055/", tmp_path)
    with pytest.raises(FileAgentError):
        file_tools.read_link("file:///C:/Windows/win.ini", tmp_path)


def test_read_link_is_offered_to_the_brain_itself():
    """The curated core is what the 1.5B student actually sees (13 entries).

    A tool that is not in that list cannot be called at all — which is how a
    perfectly good tool stays invisible. read_link earns its slot by replacing
    fetch_url for JS-rendered links, and the catalogue stays small on purpose.
    """
    import agent_loop

    specs = agent_loop.tool_specs()
    names = [s["name"] for s in specs]
    assert "read_link" in names
    description = next(s["description"] for s in specs if s["name"] == "read_link")
    assert description.strip(), "the core catalogue is Arabic-described"
    assert len(specs) <= 16, "the core must stay small or tool-calling degrades"


def test_the_tool_rejects_an_unknown_engine(tmp_path):
    from file_agent.file_tools import FileAgentError

    with pytest.raises(FileAgentError):
        file_tools.read_link("https://example.com", tmp_path, engine="telepathy")


def test_the_tool_caps_the_text_it_hands_back(loopback_server, tmp_path):
    out = file_tools.read_link(loopback_server + "/post", tmp_path, max_chars=40)
    assert len(out["text"]) <= 40
    assert "text_capped" in out["caps"]
    assert out["summary"]


# ————— 7. the setup script —————

def test_the_setup_script_installs_into_its_own_venv():
    bat = (REPO / "scripts" / "setup_reach.bat")
    assert bat.is_file()
    raw = bat.read_bytes()
    assert all(b < 128 for b in raw), "cmd.exe hygiene: ASCII only"
    assert raw.count(b"\r\n") == raw.count(b"\n"), "cmd.exe hygiene: CRLF"
    text = raw.decode("ascii")
    assert "reach-venv" in text
    assert "playwright" in text
    assert "camoufox" in text
    assert ".venv\\reach-venv" not in text, "never the training venv"


def test_reach_stays_stdlib_only():
    """No third-party import may creep into the module the agent loads."""
    source = Path(reach.__file__).read_text(encoding="utf-8")
    for banned in ("import requests", "import bs4", "from bs4", "import lxml",
                   "import playwright", "import httpx"):
        assert banned not in source, banned