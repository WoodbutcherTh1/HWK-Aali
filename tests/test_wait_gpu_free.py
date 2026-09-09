"""Unit tests for the shared GPU gate (scripts/wait_gpu_free.py).

Every nvidia-smi call goes through _run(); the tests mock exactly that seam
with canned command output and cover the three decision branches the
2026-09-09 OOM chain depends on:
  - BUSY: a python-family compute process holds the card (block)
  - IDLE: VRAM under threshold and no python compute (GO)
  - FAIL-CLOSED: nvidia-smi unavailable or max-wait elapsed (block, exit 2)

CPU-only, no real nvidia-smi calls, log output redirected to tmp dirs.
Run:  .venv/Scripts/python -m pytest tests/test_wait_gpu_free.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import wait_gpu_free as gate  # noqa: E402


# ---------------------------------------------------------------------------
# _run seam: fake nvidia-smi driven by canned output lines
# ---------------------------------------------------------------------------

class FakeNvidiaSmi:
    """Stands in for _run: returns canned text per query, or None = the
    command failed / was unreadable (the fail-closed input)."""

    def __init__(self, vram: str | None = "6651",
                 apps: str | None = "", fail_vram: bool = False,
                 fail_apps: bool = False) -> None:
        self.vram, self.apps = vram, apps
        self.fail_vram, self.fail_apps = fail_vram, fail_apps
        self.vram_calls = 0
        self.apps_calls = 0

    def __call__(self, cmd: tuple[str, ...]) -> str | None:
        if cmd == gate.QUERY_VRAM:
            self.vram_calls += 1
            if self.fail_vram or self.vram is None:
                return None
            return self.vram
        if cmd == gate.QUERY_APPS:
            self.apps_calls += 1
            if self.fail_apps or self.apps is None:
                return None
            return self.apps
        return None


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch) -> None:
    """Never touch the real D:/hwk-data log from tests."""
    monkeypatch.setattr(gate, "LOG", tmp_path / "gpu_gate.log")


# ---------------------------------------------------------------------------
# vram_used_mib / python_compute_pids: output parsing
# ---------------------------------------------------------------------------

def test_vram_used_mib_parses_the_value(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="6651"))
    assert gate.vram_used_mib() == 6651


def test_vram_used_mib_takes_the_last_line(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="  123 \n 6651 "))
    assert gate.vram_used_mib() == 6651


def test_vram_used_mib_none_when_query_fails(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram=None))
    assert gate.vram_used_mib() is None


def test_vram_used_mib_none_on_garbage(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="not-a-number"))
    assert gate.vram_used_mib() is None


def test_pids_parse_python_soup_and_ptxas(monkeypatch) -> None:
    apps = "\n".join([
        "16888, C:\\Users\\HmamK\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
        "2000, D:\\hwk-tools\\soup-venv\\Scripts\\soup.exe",
        "3001, ptxas",
    ])
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(apps=apps))
    assert gate.python_compute_pids() == [16888, 2000, 3001]


def test_pids_ignore_browsers_and_desktop(monkeypatch) -> None:
    # On Windows the desktop compositor holds CUDA contexts too - they are
    # not trainers and must never block a launch.
    apps = "\n".join([
        "111, C:\\Windows\\explorer.exe",
        "222, C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application\\msedgewebview2.exe",
        "333, C:\\Windows\\System32\\ShellHost.exe",
    ])
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(apps=apps))
    assert gate.python_compute_pids() == []


def test_pids_skip_malformed_lines(monkeypatch) -> None:
    apps = "\n".join([
        "",                      # empty line
        "solo-line-no-comma",    # fewer than 2 parts
        "abc, python.exe",       # non-numeric pid
        "12, soup.exe",
    ])
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(apps=apps))
    assert gate.python_compute_pids() == [12]


def test_python_marker_matches_substring_conservatively(monkeypatch) -> None:
    # Deliberate over-match (fail-closed direction): anything with 'python'
    # in the path blocks. Blocking extra names is safe; missing a trainer
    # OOM'd the pipeline (2026-09-08 night).
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(apps="44, C:\\tools\\pythonlibs\\helper.exe"))
    assert gate.python_compute_pids() == [44]


def test_pids_none_when_apps_query_fails(monkeypatch) -> None:
    # None = cannot verify - card_is_safe must block on it (fail-closed),
    # never read it as 'no processes running'.
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(apps=None))
    assert gate.python_compute_pids() is None


# ---------------------------------------------------------------------------
# card_is_safe: the three branches + guards
# ---------------------------------------------------------------------------

def test_safe_when_idle(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="577", apps=""))
    ok, reason = gate.card_is_safe()
    assert ok is True
    assert "577" in reason and "no python compute" in reason


def test_busy_when_python_compute_holds_the_card(monkeypatch) -> None:
    apps = "16888, C:\\Python312\\python.exe"
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="5701", apps=apps))
    ok, reason = gate.card_is_safe()
    assert ok is False
    assert "16888" in reason


def test_busy_when_vram_over_threshold_even_without_python(monkeypatch) -> None:
    # A served 4.5GB adapter with a dead-looking process list (or a graphic
    # app holding VRAM): the level check is the second guard.
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="4500", apps=""))
    ok, reason = gate.card_is_safe()
    assert ok is False
    assert "VRAM busy" in reason


def test_threshold_is_configurable(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="4000", apps=""))
    ok, _ = gate.card_is_safe(threshold_mib=4500)
    assert ok is True


def test_failclosed_when_nvidia_smi_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram=None))
    ok, reason = gate.card_is_safe()
    assert ok is False
    assert "nvidia-smi unavailable" in reason


def test_failclosed_even_when_only_one_query_fails(monkeypatch) -> None:
    # VRAM answers but the process list does not: cannot verify -> block.
    monkeypatch.setattr(gate, "_run",
                        FakeNvidiaSmi(vram="400", fail_apps=True))
    ok, _ = gate.card_is_safe()
    assert ok is False


def test_training_log_guard_blocks_a_fresh_log(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="400", apps=""))
    monkeypatch.setattr(gate, "training_log_idle_minutes", lambda: 3.0)
    ok, reason = gate.card_is_safe(require_training_log_idle=True)
    assert ok is False
    assert "training.log" in reason


def test_training_log_guard_passes_when_stale(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="400", apps=""))
    monkeypatch.setattr(gate, "training_log_idle_minutes", lambda: 20.0)
    ok, _ = gate.card_is_safe(require_training_log_idle=True)
    assert ok is True


def test_training_log_guard_passes_when_no_log_exists(monkeypatch) -> None:
    # None = nothing to protect (no training.log yet).
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="400", apps=""))
    monkeypatch.setattr(gate, "training_log_idle_minutes", lambda: None)
    ok, _ = gate.card_is_safe(require_training_log_idle=True)
    assert ok is True


def test_training_log_guard_not_active_by_default(monkeypatch) -> None:
    # soup_pipeline applies the log guard itself on top of the gate; the
    # plain gate must not require it.
    monkeypatch.setattr(gate, "_run", FakeNvidiaSmi(vram="400", apps=""))
    monkeypatch.setattr(gate, "training_log_idle_minutes", lambda: 0.0)
    ok, _ = gate.card_is_safe()
    assert ok is True


# ---------------------------------------------------------------------------
# wait_until_safe: polling loop, hook, timeout
# ---------------------------------------------------------------------------

def test_wait_returns_immediately_when_safe(monkeypatch) -> None:
    fake = FakeNvidiaSmi(vram="577", apps="")
    monkeypatch.setattr(gate, "_run", fake)
    seen: list[str] = []
    ok, reason = gate.wait_until_safe(poll_s=0.01, on_wait=seen.append)
    assert ok is True
    assert seen == []          # never had to wait
    assert fake.apps_calls == 1  # single check, no polling


def test_wait_becomes_safe_after_busy_period(monkeypatch) -> None:
    fake = FakeNvidiaSmi(vram="5701")
    fake.apps = "16888, C:\\Python312\\python.exe"

    def run_with_recovery(cmd: tuple[str, ...]) -> str | None:
        # busy for the first 8 checks, then the trainer exits
        if fake.apps_calls > 8:
            fake.apps = ""
            fake.vram = "577"
        return fake(cmd)

    monkeypatch.setattr(gate, "_run", run_with_recovery)
    seen: list[str] = []
    ok, reason = gate.wait_until_safe(poll_s=0.01, timeout_s=30, on_wait=seen.append)
    assert ok is True
    assert seen and all(isinstance(s, str) and s for s in seen)


def test_wait_times_out_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_run",
                        FakeNvidiaSmi(vram="5701", apps="16888, python.exe"))
    seen: list[str] = []
    ok, reason = gate.wait_until_safe(poll_s=0.02, timeout_s=0.1, on_wait=seen.append)
    assert ok is False
    assert "16888" in reason   # last reason, not a generic message
    assert seen                # the hook observed the retries


def test_wait_polls_both_queries_per_cycle(monkeypatch) -> None:
    fake = FakeNvidiaSmi(vram="9999", apps="")
    monkeypatch.setattr(gate, "_run", fake)
    gate.wait_until_safe(poll_s=0.01, timeout_s=0.08)
    assert fake.vram_calls >= 2 and fake.apps_calls >= 2


# ---------------------------------------------------------------------------
# CLI: exit-code contract (exit 2 = NO GO; --allow-degraded overrides)
# ---------------------------------------------------------------------------

def _cli(monkeypatch, argv: list[str], safe: bool, reason: str) -> int:
    monkeypatch.setattr(sys, "argv", ["wait_gpu_free.py", *argv])
    monkeypatch.setattr(gate, "wait_until_safe",
                        lambda **_kw: (safe, reason))
    return gate.main()


def test_cli_go_exits_zero(monkeypatch, capsys) -> None:
    assert _cli(monkeypatch, [], True, "VRAM idle: 577 MiB") == 0
    assert "GO:" in capsys.readouterr().out


def test_cli_block_exits_two(monkeypatch, capsys) -> None:
    assert _cli(monkeypatch, [], False, "python/soup compute on GPU: pids [1]") == 2
    assert "BLOCK (exit 2)" in capsys.readouterr().out


def test_cli_allow_degraded_launches_when_blocked(monkeypatch, capsys) -> None:
    assert _cli(monkeypatch, [], False, "VRAM busy") == 2                    # no flag
    assert _cli(monkeypatch, ["--allow-degraded"], False, "VRAM busy") == 0  # flag
    assert "degraded=true" in capsys.readouterr().out


def test_cli_logs_its_decision(tmp_path: Path, monkeypatch) -> None:
    log_file = tmp_path / "gpu_gate.log"
    monkeypatch.setattr(gate, "LOG", log_file)
    _cli(monkeypatch, [], True, "VRAM idle: 577 MiB")
    _cli(monkeypatch, [], False, "VRAM busy: 6651 MiB")
    text = log_file.read_text(encoding="utf-8")
    assert "GO: VRAM idle: 577 MiB" in text
    assert "BLOCK (exit 2): VRAM busy: 6651 MiB" in text
