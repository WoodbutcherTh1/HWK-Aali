"""Tests for the 2026-09-22 night session.

Three fixes/features, all pinned by tests:
1. launch_detached .py wrapping (WinError 193 lesson: every .bat launcher
   passed a bare train_scratch.py and the 23:26 relaunch attempts died
   "%1 is not a valid Win32 application").
2. print_file tool (owner request: a printer is attached to the PC/network;
   printing is a PHYSICAL action -> CONFIRM_REQUIRED + guest-blocked).
3. Bigger memory injection window (owner request: "more memory") - 25 -> 40
   entries with a character cap.
4. Phase D watchdog stage table (TDR auto-restart + stage advance must never
   launch stage 3 without the new corpora actually tokenized).

CPU-only, no real printer, no GPU, no real spawns.

Run:
    .venv/Scripts/python -m pytest tests/test_night_2026_09_22.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "file-agent"))

import launch_detached as ld  # noqa: E402


# ---------------------------------------------------------------------------
# 1. launch_detached: a bare .py target is wrapped with the repo's venv python
#    (the 23:26 WinError 193 lesson - now enforced in code, not in docs)
# ---------------------------------------------------------------------------

def test_build_command_wraps_bare_py_script(tmp_path: Path) -> None:
    cmd = ld.build_command(["train_scratch.py", "--resume", "--context", "4096"])
    assert cmd[0].endswith("python.exe")
    assert Path(cmd[0]).is_absolute()
    assert cmd[1] == "train_scratch.py"          # script becomes argument 1
    assert cmd[2:] == ["--resume", "--context", "4096"]


def test_build_command_still_accepts_real_exe(tmp_path: Path) -> None:
    fake_exe = tmp_path / "python.exe"
    fake_exe.write_text("", encoding="utf-8")
    cmd = ld.build_command([str(fake_exe), "train_scratch.py"])
    assert cmd[0] == str(fake_exe)
    assert cmd[1] == "train_scratch.py"
    assert not cmd[0].endswith("train_scratch.py")


# ---------------------------------------------------------------------------
# 2. print_file: registry invariants + behaviour with the spooler mocked out
# ---------------------------------------------------------------------------

@pytest.fixture()
def ft():
    import file_agent.file_tools as file_tools
    return file_tools


def test_print_file_registered_everywhere(ft) -> None:
    assert "print_file" in ft._FUNCTIONS
    assert ft.TOOL_EXECUTION.get("print_file") == "client"
    assert "print_file" in ft.CONFIRM_REQUIRED
    assert ft.confirm_required("print_file") is True
    names = {d["function"]["name"] for d in ft._DEFINITIONS}
    assert "print_file" in names


def test_print_file_never_runs_without_confirmation_gate(ft) -> None:
    # defence in depth: the tool is in the same always-confirm set as
    # run_command/machine_ops and can never be treated as a read-only call.
    from file_agent.file_tools import CONFIRM_REQUIRED
    assert {"run_command", "machine_ops", "print_file"} <= CONFIRM_REQUIRED


def test_print_file_rejects_unsupported_format(tmp_path: Path, ft) -> None:
    target = tmp_path / "doc.docx"
    target.write_bytes(b"PK\x03\x04")
    with pytest.raises(ft.FileAgentError, match="not supported"):
        ft.print_file("doc.docx", tmp_path)


def test_print_file_text_uses_print_command_with_queue(
        tmp_path: Path, monkeypatch, ft) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello printer", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    class _Result:
        returncode = 0
        stdout = "queued"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    monkeypatch.setattr(ft, "_printer_names", lambda: ["HP LaserJet"])
    out = ft.print_file("hello.txt", tmp_path)
    assert out["ok"] is True
    assert out["printer"] == "HP LaserJet"
    assert captured["cmd"][0] == "print"
    assert "/d:\\\\HP LaserJet" in captured["cmd"]


def test_print_file_pdf_routes_through_shell_verb(
        tmp_path: Path, monkeypatch, ft) -> None:
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF-1.4 fake")
    captured: dict[str, list[str]] = {}

    class _Result:
        returncode = 0
        stdout = "queued"
        stderr = ""

    monkeypatch.setattr(ft.subprocess, "run",
                        lambda cmd, **kw: (captured.update(cmd=cmd), _Result())[1])
    monkeypatch.setattr(ft, "_printer_names", lambda: ["PDF24"])
    out = ft.print_file("report.pdf", tmp_path)
    assert out["ok"] is True
    assert captured["cmd"][0] == "powershell"
    # single-quoted path (PowerShell-safe) - never json.dumps double quotes
    assert f"-FilePath '{target}'" in captured["cmd"][3]


def test_print_file_spooler_failure_raises_honestly(
        tmp_path: Path, monkeypatch, ft) -> None:
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "spooler says no"

    monkeypatch.setattr(ft.subprocess, "run", lambda cmd, **kw: _Result())
    monkeypatch.setattr(ft, "_printer_names", lambda: ["P1"])
    with pytest.raises(ft.FileAgentError, match="spooler"):
        ft.print_file("doc.txt", tmp_path)


# ---------------------------------------------------------------------------
# 3. Memory: bigger injection window with a character cap
# ---------------------------------------------------------------------------

def test_memory_render_window_widened() -> None:
    from file_agent import memory
    assert memory.MAX_RENDER_ENTRIES == 40
    assert memory.MAX_RENDER_CHARS >= 4000


def test_memory_render_respects_entry_and_char_caps(
        tmp_path: Path, monkeypatch) -> None:
    from file_agent import memory
    monkeypatch.setenv(memory.MEMORY_DIR_ENV, str(tmp_path))
    for i in range(60):
        memory.remember(
            f"preference {i} " + "word " * 40,   # ~250 chars per entry
            kind="preference", topic=f"t{i}")
    block = memory.render_block()
    lines = block.count("\n- [")
    # the widening: more than the old 25-entry window...
    assert 25 < lines <= 40
    # ...and the token-safety char cap bounds the whole block either way
    assert len(block) < memory.MAX_RENDER_CHARS + 2000


# ---------------------------------------------------------------------------
# 4. Phase D watchdog: stage table + the stage-3 deferral guard
# ---------------------------------------------------------------------------

def _load_watchdog():
    spec = importlib.util.spec_from_file_location(
        "phase_d_watchdog", str(ROOT / "scripts" / "phase_d_watchdog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def wd(tmp_path, monkeypatch):
    mod = _load_watchdog()
    # never spawn anything real, never read the production lock
    monkeypatch.setattr(mod, "launch_detached",
                        lambda command, log_base: None)
    monkeypatch.setattr(mod, "TOKENIZE_LOCK", tmp_path / "tokenize.pidlock")
    monkeypatch.setattr(mod, "WATCH_LOG", tmp_path / "watchdog.log")
    monkeypatch.setattr(mod, "TRAIN_LOG", tmp_path / "phase_d_train.log")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
    return mod


def test_watchdog_stage_table_chains(wd) -> None:
    stages = wd.STAGES
    assert [s["stage"] for s in stages] == [1, 2, 3]
    assert stages[0]["source_dir"] is None            # stage 1 resumes in place
    assert stages[1]["source_dir"] == stages[0]["output_dir"]
    assert stages[2]["source_dir"] == stages[1]["output_dir"]
    assert "code" in stages[1]["corpora"]             # stage 2 ADDS code


def test_watchdog_stage_command_contract(wd) -> None:
    cmd = wd.build_stage_command(wd.STAGES[1])
    joined = " ".join(cmd)
    assert cmd[0].endswith("python.exe")              # never a bare .py (193!)
    assert "--dropout 0.0" in joined                  # checkpoint payload config
    assert "--resume" in joined
    assert "code" in joined


def test_watchdog_never_launches_stage3_without_new_corpora(
        wd, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wd, "tokens_root", lambda: tmp_path)   # nothing tokenized
    assert wd.stage3_corpora_arg() is None
    monkeypatch.setattr(wd, "load_state", lambda: {
        "stage": 2, "max_steps": 4000,
        "output_dir": "D:/hwk-models/phase-d-code",
        "launched_at": "2026-09-22T01:00:00"})
    monkeypatch.setattr(wd, "last_step", lambda: 4001)         # stage 2 COMPLETE
    launched: list[dict] = []
    monkeypatch.setattr(wd, "launch_stage", lambda s: launched.append(s) or True)
    action = wd.check_cycle(heartbeat=False)
    assert launched == []                       # the guard must hold
    assert "deferred" in action


def test_watchdog_builds_stage3_from_available_corpora(
        wd, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wd, "tokens_root", lambda: tmp_path)
    for name in ("law", "medical", "mmlu"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "train_00000.bin").write_bytes(b"")
    arg = wd.stage3_corpora_arg()
    assert arg == "pile,arabic,instruct,orca_math,reasoning,code,law,medical,mmlu"


def test_watchdog_tokenizer_lock_blocks_second_launch(
        wd, tmp_path, monkeypatch) -> None:
    # the 01:44 race: two tokenizers corrupt shards. A live-lock pid must
    # suppress ensure_tokenization entirely.
    lock = tmp_path / "tokenize.pidlock"
    lock.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(wd, "tokens_root", lambda: tmp_path)

    def fake_running():
        return True, 999999

    monkeypatch.setattr(wd, "tokenizer_running", fake_running)
    spawned: list[list[str]] = []
    monkeypatch.setattr(wd, "launch_detached",
                        lambda command, log_base: spawned.append(command) or 1)
    wd.ensure_tokenization()
    assert spawned == []
