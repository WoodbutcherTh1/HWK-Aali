"""Tests for aali_node.confirm — the human-confirmation layer.

Safety property of the test suite itself: NO test ever pops a real dialog.
The native path is exercised through the `_show_confirm_dialog` seam
(monkeypatched to return immediately), the MessageBoxW fallback through a
monkeypatched ctypes.windll.user32, and the template packing through pure
parse-back checks — the "user never clicked" case (60s silence) always
resolves to a denial, in milliseconds.
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
# native dialog — the custom-dialog seam (no real window in tests)
# ---------------------------------------------------------------------------
def test_native_confirm_custom_dialog_yes(
        monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_dialog(title, ar, en, timeout):  # noqa: ANN001
        captured.update(title=title, ar=ar, en=en, timeout=timeout)
        return CF._IDYES

    monkeypatch.setattr(CF, "_show_confirm_dialog", fake_dialog)
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is True
    assert "x" in captured["ar"] and "x" in captured["en"]
    assert captured["timeout"] == CF.CONFIRM_TIMEOUT_SEC
    assert "Aali" in captured["title"] and "آلي" in captured["title"]


def test_native_confirm_no_or_close_denies(
        monkeypatch: pytest.MonkeyPatch) -> None:
    for code in (CF._IDNO, 0):  # IDNO, or anything that is not IDYES
        monkeypatch.setattr(
            CF, "_show_confirm_dialog",
            lambda *a, _c=code: _c)  # noqa: ARG005
        assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_native_confirm_timeout_denies(monkeypatch: pytest.MonkeyPatch,
                                       ) -> None:
    """No answer before the deadline = deny (the 60s rule, in ms).

    The real timer lives inside the dialog; here the seam returns what the
    timer path returns (_IDNO) — the contract under test is that silence
    resolves to a denial, never an approval.
    """
    monkeypatch.setattr(CF, "_show_confirm_dialog",
                        lambda *a: CF._IDNO)
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_native_confirm_requires_false_short_circuits(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # pragma: no cover — must never be reached
        raise AssertionError("dialog shown for a non-confirmed call")

    monkeypatch.setattr(CF, "_show_confirm_dialog", boom)
    assert CF.native_confirm(False, "delete_file", {"path": "x"}) is True


def test_native_confirm_custom_crash_falls_back_to_messagebox(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken custom dialog degrades to the classic box, still fail-safe."""
    def boom(*a, **k):
        raise OSError("no desktop")

    monkeypatch.setattr(CF, "_show_confirm_dialog", boom)
    _patch_message_box(monkeypatch, lambda *a, **k: 6)  # IDYES
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is True


def test_native_confirm_fallback_denies_or_silence(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback box: IDNO, or the watcher thread never gets an answer."""
    monkeypatch.setattr(CF, "_show_confirm_dialog",
                        lambda *a: (_ for _ in ()).throw(OSError()))
    _patch_message_box(monkeypatch, lambda *a, **k: 7)  # IDNO
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False

    def never_join(self, timeout=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(threading.Thread, "join", never_join)
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_native_confirm_crash_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    # a ctypes module with no windll (non-Windows / stripped environment):
    # custom dialog raises → fallback raises → deny, never crash
    fake_ctypes = types.ModuleType("ctypes")
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    assert CF.native_confirm(True, "delete_file", {"path": "x"}) is False


def test_fallback_messagebox_text_carries_both_languages(
        monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_box(_hwn, text, _cap, _flags):  # noqa: ANN001
        captured["text"] = text
        return 7

    monkeypatch.setattr(CF, "_show_confirm_dialog",
                        lambda *a: (_ for _ in ()).throw(OSError()))
    _patch_message_box(monkeypatch, fake_box)
    CF.native_confirm(True, "run_command", {"command": "echo hi"})
    assert "echo hi" in captured["text"]
    assert "Aali" in captured["text"]


# ---------------------------------------------------------------------------
# dialog template packing — pure, headless, parsed back the way Windows does
# ---------------------------------------------------------------------------
def _parse_dialog_template(buf: bytes) -> dict:
    import struct

    off = 0
    style, ex = struct.unpack_from("<II", buf, off)
    off += 8
    cdit, x, y, cx, cy = struct.unpack_from("<HHHHH", buf, off)
    off += 10
    menu, wclass = struct.unpack_from("<HH", buf, off)
    off += 4

    def rd_wz(o: int) -> tuple[str, int]:
        chars: list[str] = []
        while True:
            unit = int.from_bytes(buf[o:o + 2], "little")
            o += 2
            if unit == 0:
                return "".join(chars), o
            chars.append(chr(unit))

    title, off = rd_wz(off)
    off += 2  # point size
    fontname, off = rd_wz(off)
    items = []
    for _ in range(cdit):
        while off % 4:
            off += 1
        istyle, _iex = struct.unpack_from("<II", buf, off)
        off += 8
        ix, iy, icx, icy, iid = struct.unpack_from("<HHHHH", buf, off)
        off += 10
        cls = struct.unpack_from("<H", buf, off)[0]
        off += 2
        if cls == 0xFFFF:
            cls = struct.unpack_from("<H", buf, off)[0]
            off += 2
        else:
            _cls, off = rd_wz(off)
            cls = None
        # sz_Or_Ord: 0x0000 = none, 0xFFFF + ordinal, else null-terminated
        text_ord = struct.unpack_from("<H", buf, off)[0]
        if text_ord == 0xFFFF:
            off += 2
            text = struct.unpack_from("<H", buf, off)[0]
            off += 2
        else:
            text, off = rd_wz(off)  # first word belongs to the string
        off += 2  # creation-data size
        items.append({"id": iid, "style": istyle, "x": ix, "y": iy,
                      "cx": icx, "cy": icy, "cls": cls, "text": text})
    return {"style": style, "ex": ex, "cdit": cdit, "cx": cx, "cy": cy,
            "menu": menu, "wclass": wclass, "title": title,
            "font": fontname, "items": items}


def test_pack_dialog_template_is_valid_and_safe_first() -> None:
    t = _parse_dialog_template(CF._pack_dialog_template("t", rtl=False))
    assert t["cdit"] == 2
    assert t["title"] == "t"
    assert t["font"] == "Segoe UI"
    assert t["menu"] == 0 and t["wclass"] == 0
    # both items are owner-draw buttons with the deny/allow ids
    ids = [i["id"] for i in t["items"]]
    assert ids == [CF._IDNO, CF._IDYES]  # No first = first tabstop
    for item in t["items"]:
        assert item["cls"] == 0x0080  # button class atom
        assert item["style"] & CF._BS_OWNERDRAW
    no, yes = t["items"]
    assert no["text"] == CF._BTN_NO_LABEL
    assert yes["text"] == CF._BTN_YES_LABEL
    # both sit on the same row at the bottom of the dialog
    assert no["y"] == yes["y"]
    assert no["y"] + no["cy"] <= t["cy"]
    assert yes["x"] + yes["cx"] <= t["cx"]


def test_pack_dialog_template_rtl_sets_mirroring() -> None:
    ltr = _parse_dialog_template(CF._pack_dialog_template("t", rtl=False))
    rtl = _parse_dialog_template(CF._pack_dialog_template("t", rtl=True))
    assert not ltr["ex"] & CF._WS_EX_LAYOUTRTL
    assert rtl["ex"] & CF._WS_EX_LAYOUTRTL
    assert rtl["ex"] & CF._WS_EX_TOPMOST and ltr["ex"] & CF._WS_EX_TOPMOST


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
