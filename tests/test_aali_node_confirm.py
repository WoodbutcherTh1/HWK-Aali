"""Tests for aali_node.confirm — the human-confirmation layer.

Safety property of the test suite itself: NO test ever pops a real dialog.
The native path is exercised through a monkeypatched ctypes.windll.user32
MessageBoxW (returning immediately), and the timeout path through a fake
thread whose join returns with no answer — the "user never clicked" case,
i.e. exactly the 60s-timeout contract in milliseconds.
"""
from __future__ import annotations

import builtins
import threading
import types
from typing import Any

import pytest

from aali_node import confirm as CF
from aali_node import sandbox as SB
from file_agent import protocol as P


def _call(tool: str, args: dict, **kw) -> dict:
    return P.sign_message(P.make_tool_call(tool, args, **kw), b"k")


# ---------------------------------------------------------------------------
# describe: honest, bilingual, path-bearing summaries
# ---------------------------------------------------------------------------
def test_describe_run_command() -> None:
    ar, en = CF._describe("run_command", {"command": "pytest -q"})
    assert "pytest -q" in ar and "pytest -q" in en


def test_describe_delete_arabic_first() -> None:
    ar, en = CF._describe("delete_file", {"path": "x.txt"})
    assert "x.txt" in ar and "حذف" in ar
    assert "Delete file: x.txt" == en


def test_describe_unknown_tool_still_summarizes() -> None:
    ar, en = CF._describe("whatever", {})
    assert "whatever" in ar and "whatever" in en


# ---------------------------------------------------------------------------
# native dialog
# ---------------------------------------------------------------------------
def test_native_confirm_user_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_message_box(monkeypatch, lambda *a, **k: 6)  # IDYES
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is True


def test_native_confirm_user_no(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_message_box(monkeypatch, lambda *a, **k: 7)  # IDNO
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_native_confirm_timeout_denies(monkeypatch: pytest.MonkeyPatch,
                                       ) -> None:
    """No answer before the deadline = deny (the 60s rule, in ms)."""
    def never_join(self, timeout=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(threading.Thread, "join", never_join)
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_native_confirm_requires_false_short_circuits(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # pragma: no cover — must never be reached
        raise AssertionError("dialog shown for a non-confirmed call")

    _patch_message_box(monkeypatch, boom)
    assert CF.native_confirm(False, "delete_file", {"path": "x"}) is True


def test_native_confirm_crash_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    # a ctypes module with no windll (non-Windows / stripped environment):
    # attribute access raises inside native_confirm → deny, never crash
    fake_ctypes = types.ModuleType("ctypes")
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_native_dialog_text_carries_both_languages(
        monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_box(_hwn, text, _cap, _flags):  # noqa: ANN001
        captured["text"] = text
        return 7

    _patch_message_box(monkeypatch, fake_box)
    CF.native_confirm(True, "run_command", {"command": "echo hi"})
    assert "echo hi" in captured["text"]
    assert "Aali" in captured["text"]


# ---------------------------------------------------------------------------
# console prompt
# ---------------------------------------------------------------------------
def test_console_confirm_yes_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    for answer in ("y", "YES", "نعم", " ن "):
        monkeypatch.setattr(builtins, "input", lambda _p="": answer)
        assert CF.console_confirm(True, "delete_file", {"path": "x"}) is True


def test_console_confirm_no_and_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtins, "input", lambda _p="": "n")
    assert CF.console_confirm(True, "delete_file", {"path": "x"}) is False
    monkeypatch.setattr(builtins, "input",
                        lambda _p="": (_ for _ in ()).throw(EOFError()))
    assert CF.console_confirm(True, "delete_file", {"path": "x"}) is False


def test_console_confirm_requires_false_short_circuits() -> None:
    assert CF.console_confirm(False, "delete_file", {"path": "x"}) is True


# ---------------------------------------------------------------------------
# hook selection
# ---------------------------------------------------------------------------
def test_hook_skips_asking_when_not_required() -> None:
    hook = CF.make_confirm_hook(use_native=True)
    assert hook(False, "delete_file", {"path": "x"}) is True


def test_hook_selects_console_without_desktop(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CF, "_interactive_desktop", lambda: False)
    called: dict[str, bool] = {}

    def fake_console(requires, tool, args):  # noqa: ANN001
        called["console"] = True
        return True

    monkeypatch.setattr(CF, "console_confirm", fake_console)
    hook = CF.make_confirm_hook(use_native=True)
    assert hook(True, "delete_file", {"path": "x"}) is True
    assert called.get("console") is True


def test_hook_uses_native_on_desktop_when_wanted(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CF, "_interactive_desktop", lambda: True)
    called: dict[str, str] = {}

    def fake_native(requires, tool, args):  # noqa: ANN001
        called["native"] = True
        return True

    monkeypatch.setattr(CF, "native_confirm", fake_native)
    hook = CF.make_confirm_hook(use_native=True)
    assert hook(True, "delete_file", {"path": "x"}) is True
    assert called.get("native") is True


def test_hook_console_mode_when_native_not_wanted(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CF, "_interactive_desktop", lambda: True)
    called: dict[str, str] = {}

    def fake_console(requires, tool, args):  # noqa: ANN001
        called["console"] = True
        return False

    monkeypatch.setattr(CF, "console_confirm", fake_console)
    hook = CF.make_confirm_hook(use_native=False)
    assert hook(True, "delete_file", {"path": "x"}) is False
    assert called.get("console") is True


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------
def test_daemon_run_node_accepts_native_confirm_flag(
        monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """--native-confirm reaches the sandbox hook (websockets-absent path)."""
    from aali_node import daemon as ND

    captured: dict[str, Any] = {}

    class FakeSandbox(SB.SecureSandbox):
        def __init__(self, root, *, allow_commands=True, confirm_hook=None):
            captured["hook"] = confirm_hook
            captured["allow"] = allow_commands

    def fake_import(name, *a, **k):  # noqa: ANN001, ANN002
        if name == "websockets":
            raise ImportError("not installed in this venv")
        return real_import(name, *a, **k)

    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(ND.SB, "SecureSandbox", FakeSandbox)
    rc = ND.run_node("ws://127.0.0.1:1", "tok", tmp_path / "ws",
                     allow_commands=True, native_confirm=True)
    assert rc == 2  # websockets missing → clean exit after wiring
    assert captured["hook"] is not None
    assert captured["allow"] is True


# ---------------------------------------------------------------------------
# end-to-end: sandbox + confirm hook together (headless auto-deny intact)
# ---------------------------------------------------------------------------
def test_sandbox_with_real_console_hook_denies_on_eof(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Hook wired through make_confirm_hook; EOF at the prompt = denial."""
    import tempfile
    from pathlib import Path

    monkeypatch.setattr(CF, "_interactive_desktop", lambda: False)
    box = SB.SecureSandbox(Path(tempfile.mkdtemp()) / "ws",
                           confirm_hook=CF.make_confirm_hook(use_native=True))
    monkeypatch.setattr(builtins, "input",
                        lambda _p="": (_ for _ in ()).throw(EOFError()))
    out = box.execute(_call("delete_file", {"path": "a.txt"}))
    assert out["status"] == "denied"


def _patch_message_box(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    """Fake ctypes.windll.user32.MessageBoxW with *fn*."""
    user32 = types.SimpleNamespace(MessageBoxW=fn)
    windll = types.SimpleNamespace(user32=user32)
    fake_ctypes = types.ModuleType("ctypes")
    fake_ctypes.windll = windll
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
