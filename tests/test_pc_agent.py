"""Tests for the PC control agent (``scripts/pc_agent.py``).

The agent is the hands behind Jarvis' resources/logs/power on the PC, so the
tests are mostly about REFUSALS: an unauthenticated control service on the LAN
would be worse than none. The server runs in-process on an ephemeral port and
everything stateful (log roots, GPU subprocess, audit log) is pointed into
tmp_path, so no test ever touches the real D:/hwk-data.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pc_agent import (
    AgentHandler,
    collect_gpu,
    collect_ram,
    power_action,
    tail_log,
    whitelisted_logs,
)

TOKEN = "test-token-12345"


class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setattr("pc_agent.TOKEN", TOKEN)
    monkeypatch.setattr("pc_agent.LOG_ROOTS", (tmp_path / "logs",))
    monkeypatch.setattr("pc_agent.AUDIT_LOG", tmp_path / "audit.log")
    (tmp_path / "logs").mkdir()
    srv = _Server(AgentHandler)
    srv.start()
    yield srv, tmp_path
    srv.stop()


def _post(port: int, path: str, payload: dict, token: str | None = TOKEN, host: str = "127.0.0.1") -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Host": host},
    )
    if token is not None:
        req.add_header("X-Aali-Agent-Token", token)
    return _send(req)


def _send(req: urllib.request.Request) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(port: int, path: str, host: str = "127.0.0.1") -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    req.add_header("Host", host)
    return _send(req)


# ------------------------------------------------------------------ health
def test_health_is_open_and_content_free(server):
    srv, _ = server
    status, data = _get(srv.port, "/api/health")
    assert status == 200 and data["ok"] is True
    assert data["service"] == "pc_agent"
    # content-free: nothing about disks, memory or files
    assert "drives" not in json.dumps(data) and "lines" not in json.dumps(data)


def test_unknown_routes_are_404(server):
    """The gate runs BEFORE routing: an unauthenticated caller gets 401 even
    for a route that does not exist, so the endpoint set is never revealed."""
    srv, _ = server
    status, _ = _post(srv.port, "/api/nope", {}, token=None)
    assert status == 401
    assert _post(srv.port, "/api/nope", {})[0] == 404  # authenticated: honest 404


# ------------------------------------------------------------- token gate
def test_token_matrix(server):
    """No token -> 401, wrong token -> 401, right token -> 200."""
    srv, _ = server
    assert _post(srv.port, "/api/resources", {"what": "disk"}, token=None)[0] == 401
    assert _post(srv.port, "/api/resources", {"what": "disk"}, token="wrong")[0] == 401
    status, data = _post(srv.port, "/api/resources", {"what": "disk"}, token=TOKEN)
    assert status == 200 and data["ok"] is True and "disk" in data


def test_fail_closed_without_any_token(monkeypatch, server):
    """An unconfigured token refuses EVERYTHING — fail closed, never open."""
    srv, _ = server
    monkeypatch.setattr("pc_agent.TOKEN", "")
    assert _post(srv.port, "/api/resources", {"what": "disk"}, token="")[0] == 401


def test_unauthorized_attempts_are_logged_content_free(server, tmp_path):
    srv, _ = server
    _post(srv.port, "/api/power", {"action": "shutdown", "secret": "gsk_leak"}, token=None)
    audit = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "unauthorized" in audit
    assert "gsk_leak" not in audit and "shutdown" not in audit


# ------------------------------------------------------------- validation
def test_bad_host_is_refused(server):
    """A DNS-rebinding page presents a domain name; only IP hosts pass."""
    srv, _ = server
    status, _ = _get(srv.port, "/api/health", host="aali.dpdns.org")
    assert status == 403


def test_oversized_body_is_refused(server):
    srv, _ = server
    status, data = _post(srv.port, "/api/resources", {"what": "x" * 5000})
    assert status == 400


def test_invalid_what_is_refused(server):
    srv, _ = server
    status, data = _post(srv.port, "/api/resources", {"what": "format"})
    assert status == 400 and "what" in data["error"]


# -------------------------------------------------------------- resources
def test_disk_numbers_are_real(server):
    srv, _ = server
    _, data = _post(srv.port, "/api/resources", {"what": "disk"})
    drives = data["disk"]["drives"]
    assert "C" in drives
    for info in drives.values():
        assert info["free_gb"] <= info["total_gb"]
        assert 0 <= info["free_pct"] <= 100


def test_ram_reports_structure(server):
    srv, _ = server
    _, data = _post(srv.port, "/api/resources", {"what": "ram"})
    ram = data["ram"]
    if ram["ok"]:  # Windows here; a non-Windows host reports an honest error
        assert ram["total_gb"] > 0 and ram["avail_gb"] <= ram["total_gb"]


def test_gpu_parses_real_nvidia_smi_shape(server, monkeypatch):
    srv, _ = server
    monkeypatch.setattr(
        "pc_agent._run",
        lambda argv, t: (0, "NVIDIA GeForce RTX 3070, 0, 3825, 8192, 45"),
    )
    _, data = _post(srv.port, "/api/resources", {"what": "gpu"})
    g = data["gpu"]["gpus"][0]
    assert g["name"] == "NVIDIA GeForce RTX 3070"
    assert g["mem_used_gb"] == 3.7 and g["mem_total_gb"] == 8.0


def test_gpu_missing_tool_is_honest(server, monkeypatch):
    srv, _ = server
    monkeypatch.setattr("pc_agent._run", lambda argv, t: (127, "not found"))
    _, data = _post(srv.port, "/api/resources", {"what": "gpu"})
    assert data["gpu"]["ok"] is False


def test_collect_ram_direct():
    out = collect_ram()
    if out["ok"]:
        assert out["total_gb"] > 0


def test_collect_gpu_rejects_garbage(monkeypatch):
    monkeypatch.setattr("pc_agent._run", lambda argv, t: (0, "one, two"))
    assert collect_gpu()["ok"] is False


# ------------------------------------------------------------------- logs
def test_logs_whitelist_round_trip(server, tmp_path):
    srv, tmp = server
    (tmp / "logs" / "aali_server.log").write_text(
        "line-one\nline-two\n", encoding="utf-8"
    )
    status, data = _post(srv.port, "/api/logs", {"file": "aali_server.log"})
    assert status == 200 and data["ok"] is True
    assert data["lines"][-1] == "line-two"


def test_logs_refuse_traversal(server):
    srv, _ = server
    for evil in ("../secrets.txt", "..\\secrets.txt", "sub/dir/x.log", ".", ".."):
        status, data = _post(srv.port, "/api/logs", {"file": evil})
        assert status == 404 and data["ok"] is False, evil


def test_logs_unknown_name_names_the_known_ones(server, tmp_path):
    srv, tmp = server
    (tmp / "logs" / "real.log").write_text("x\n", encoding="utf-8")
    _, data = _post(srv.port, "/api/logs", {"file": "nope.log"})
    assert data["ok"] is False and "real.log" in data["known"]


def test_logs_line_cap_is_enforced(server, tmp_path):
    srv, tmp = server
    (tmp / "logs" / "big.log").write_text(
        "\n".join(f"l{i}" for i in range(1000)) + "\n", encoding="utf-8"
    )
    _, data = _post(srv.port, "/api/logs", {"file": "big.log", "lines": 99999})
    assert len(data["lines"]) <= 200 and data["lines"][-1] == "l999"


def test_whitelisted_logs_lists_names_only(tmp_path, monkeypatch):
    monkeypatch.setattr("pc_agent.LOG_ROOTS", (tmp_path,))
    (tmp_path / "a.log").write_text("x", encoding="utf-8")
    (tmp_path / "notlog.txt").write_text("x", encoding="utf-8")
    assert whitelisted_logs() == ["a.log"]


def test_tail_log_rejects_paths_direct():
    assert tail_log("../evil")["ok"] is False
    assert tail_log("")["ok"] is False


# ------------------------------------------------------------------ power
def test_power_only_runs_the_fixed_table(monkeypatch, server):
    srv, _ = server
    seen = {}

    def fake_run(argv, timeout):
        seen["argv"] = argv
        return 0, ""

    monkeypatch.setattr("pc_agent._run", fake_run)
    status, data = _post(srv.port, "/api/power", {"action": "shutdown"})
    assert status == 200 and data["ok"] is True
    assert seen["argv"][0] == "shutdown"  # the table's command, verbatim


def test_power_refuses_anything_not_in_the_table(server):
    srv, _ = server
    status, data = _post(srv.port, "/api/power", {"action": "format c:"})
    assert status == 400 and "unknown action" in data["error"]


def test_power_action_direct_runner():
    seen = []

    def runner(argv, timeout):
        seen.append(argv)
        return 0, ""

    assert power_action("reboot", runner=runner)["ok"] is True
    assert seen[0][0] == "shutdown" or seen[0][0] == "reboot"
    assert power_action("selfdestruct", runner=runner)["ok"] is False


# ------------------------------------------------------------- audit log
def test_audit_log_stays_content_free(server, tmp_path):
    srv, tmp = server
    _post(srv.port, "/api/resources", {"what": "disk"})
    _post(srv.port, "/api/logs", {"file": "aali_server.log"})
    audit = (tmp / "audit.log").read_text(encoding="utf-8")
    assert "resources" in audit and "logs" in audit
    # no request bodies, no secrets — only kinds and fixed-enum telemetry
    assert "aali_server.log" not in audit and "gsk_" not in audit
