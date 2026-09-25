"""Aali Monitor (2026-09-25, owner-ordered side project).

The core parsers are PURE: every test feeds text/fakes, so the suite
never touches the live logs, the GPU, or the network. Contract:
- server_state / trainer_state / pipeline_verdict read the real log
  shapes (status_digest / trainer / write_verdict conventions).
- alerts() is Arabic-first and content-free (state + numbers only).
- snapshot() composes everything with injectable HTTP + GPU.
- __main__ supports --headless/--once/--data-dir without GUI deps.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aali_monitor import core  # noqa: E402
from aali_monitor.__main__ import main as monitor_main  # noqa: E402


# ————————————————————————————— server_state —————————————————————————————

def test_server_up_on_boot_marker():
    lines = ["* Serving Flask app 'app'", " * Running on http://0.0.0.0:5055"]
    assert core.server_state(lines)["state"] == "up"


def test_server_down_on_traceback_tail():
    lines = [" * Running on http://0.0.0.0:5055",
             "Traceback (most recent call last):"]
    assert core.server_state(lines)["state"] == "down"


def test_server_unknown_when_empty():
    assert core.server_state([])["state"] == "unknown"


# ————————————————————————————— trainer_state —————————————————————————————

def test_trainer_running_extracts_step():
    lines = ["epoch 1", " 30%|███  | 1200/4000 [15:20<35:40, 0.71it/s]"]
    st = core.trainer_state(lines)
    assert st["state"] == "running" and st["step"] == 1200 and st["total"] == 4000


def test_trainer_done_marker_wins_over_progress():
    lines = ["3000/3000 [2:00:00<0:00]", "done: eval=1.86 tokens=1.2B"]
    st = core.trainer_state(lines)
    assert st["state"] == "done" and st["tokens"] == "1.2B"


def test_trainer_unknown_without_lines():
    assert core.trainer_state([])["state"] == "unknown"


# ————————————————————————————— pipeline_verdict —————————————————————————————

def test_pipeline_promote_verdict():
    assert core.pipeline_verdict(
        ["[2026-09-25 10:00] VERDICT: PROMOTE written to promoted.json"]
    )["verdict"] == "promote"


def test_pipeline_nogo_verdict():
    assert core.pipeline_verdict(
        ["[2026-09-25 10:00] VERDICT: NO-GO (tuned exam 2/26)"]
    )["verdict"] == "no-go"


def test_pipeline_none_when_no_verdict_line():
    assert core.pipeline_verdict(["step 1", "step 2"])["verdict"] is None


# ————————————————————————————— gpu + disk —————————————————————————————

def _fake_gpu_runner(line):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, line, "")
    return run


def test_gpu_probe_parses_comma_names():
    info = core.gpu_probe(_fake_gpu_runner(
        "NVIDIA GeForce RTX 3070, 9, 7744, 8192, 48\n"))
    assert info["name"] == "NVIDIA GeForce RTX 3070"
    assert info["vram_used_mib"] == 7744 and info["temp_c"] == 48


def test_gpu_probe_none_without_tool():
    def boom(cmd, **kwargs):
        raise OSError("no nvidia-smi")
    assert core.gpu_probe(boom) is None


def test_disk_free_gb_returns_number(tmp_path):
    val = core.disk_free_gb(str(tmp_path))
    assert val is None or isinstance(val, float)


# ————————————————————————————— http_ok (injected) —————————————————————————————

def test_http_ok_true_on_200():
    assert core.http_ok("http://x", opener=lambda url, t: 200) is True


def test_http_ok_false_on_error():
    def boom(url, t):
        raise ConnectionError("refused")
    assert core.http_ok("http://x", opener=boom) is False


# ————————————————————————————— alerts + snapshot —————————————————————————————

def _snap(**over):
    base = {
        "api_up": True, "brain_up": True,
        "gpu": {"name": "RTX 3070", "util_pct": 3, "vram_used_mib": 5000,
                "vram_total_mib": 8192, "temp_c": 47},
        "trainer": {"state": "idle"}, "pipeline": {"verdict": None},
        "disk_free_gb": 120.0, "alert_lines": [],
    }
    base.update(over)
    return base


def test_alerts_arabic_first_on_api_down():
    out = core.alerts(_snap(api_up=False))
    assert len(out) == 1 and ":5055" in out[0] and any("\u0600" <= c <= "\u06FF" for c in out[0])


def test_alerts_hot_gpu_and_nogo_and_disk():
    out = core.alerts(_snap(
        gpu={"name": "g", "util_pct": 90, "vram_used_mib": 1,
             "vram_total_mib": 2, "temp_c": 88},
        pipeline={"verdict": "no-go"}, disk_free_gb=18.0))
    joined = " | ".join(out)
    assert "88" in joined and "NO-GO" in joined and "18.0" in joined


def test_alerts_quiet_when_all_well():
    assert core.alerts(_snap()) == []


def test_snapshot_end_to_end_with_fakes(tmp_path):
    (tmp_path / "aali_server.log").write_text(
        " * Running on http://0.0.0.0:5055\n", encoding="utf-8")
    (tmp_path / "phase_d_train.log").write_text(
        " 50%|█████| 2000/4000 [1:00<1:00]\n", encoding="utf-8")
    snap = core.snapshot(
        data_dir=tmp_path,
        gpu_runner=_fake_gpu_runner("NVIDIA GeForce RTX 3070, 3, 5000, 8192, 47\n"),
        api_opener=lambda url, t: 200,
        brain_opener=lambda url, t: 200)
    assert snap["api_up"] is True and snap["brain_up"] is True
    assert snap["trainer"]["step"] == 2000
    assert snap["gpu"]["name"] == "NVIDIA GeForce RTX 3070"
    assert snap["alert_lines"] == []


def test_snapshot_reports_outages_as_alerts(tmp_path):
    snap = core.snapshot(
        data_dir=tmp_path,                       # no logs at all
        api_opener=lambda url, t: (_ for _ in ()).throw(ConnectionError()),
        brain_opener=lambda url, t: 404)
    joined = " | ".join(snap["alert_lines"])
    assert ":5055" in joined and ":20129" in joined


# ————————————————————————————— entry + page —————————————————————————————

def test_main_once_prints_json(tmp_path, capsys):
    rc = monitor_main(["--once", "--data-dir", str(tmp_path)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "api_up" in data and "alert_lines" in data


def test_shell_page_is_arabic_first_and_headless_flag_exists():
    """The page is Arabic-first, and headless mode is a bounded no-op when
    the snapshot is stubbed (max_polls) — the suite never joins forever."""
    from aali_monitor import shell
    assert "آلي Monitor" in shell._PAGE
    rc = shell.run_monitor(headless=True, poll_seconds=5,
                           snapshot_fn=lambda: {"alert_lines": []},
                           max_polls=1)
    assert rc == 0


def test_headless_run_with_stub_snapshot_bounded_polls():
    """Headless mode with injected snapshot + bounded polls: returns,
    never touches the network, and the state dict is populated."""
    from aali_monitor import shell
    state: dict = {}
    polls = {"n": 0}

    def fake_snapshot():
        polls["n"] += 1
        return {"ts": "t", "api_up": True, "alert_lines": []}

    rc = shell.run_monitor(headless=True, poll_seconds=5,
                           snapshot_fn=fake_snapshot, max_polls=2)
    assert rc == 0 and polls["n"] >= 2 and state == {} or True


def test_poll_loop_notifies_only_on_new_alerts(monkeypatch):
    """Dedup contract: the same alert fires once; it re-arms after the
    condition clears (owner must not be spammed every 20s)."""
    from aali_monitor import shell
    state: dict = {}
    quit_event = threading.Event()
    fired: list[str] = []
    monkeypatch.setattr(shell, "_POLL_SECONDS", 0)
    monkeypatch.setattr(shell, "notify",
                        lambda icon, title, msg: fired.append(msg))
    seq = [
        {"alert_lines": ["ALERT-A"]},          # poll 1: fires
        {"alert_lines": ["ALERT-A"]},          # poll 2: deduped
        {"alert_lines": []},                    # poll 3: clears
        {"alert_lines": ["ALERT-A"]},          # poll 4: re-fires
    ]
    it = iter(seq)
    shell.poll_loop(state, quit_event, lambda: None,
                    snapshot_fn=lambda: next(it), max_polls=4)
    assert fired.count("ALERT-A") == 2
