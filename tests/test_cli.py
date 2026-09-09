"""Tests for the Aali CLI input layer (aali_cli.py).

The LineEditor is exercised through its `keys=` injection seam — the same
key names the real readers emit ("UP", "TAB", plain characters…), so the
tests run the real editing logic without a terminal. Exhausted injection
raises KeyboardInterrupt inside read(), which read() itself converts to a
cancel (returns None).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aali_cli as cli  # noqa: E402


# ----------------------------------------------------------------- themes
def test_completer_offers_themes() -> None:
    """TAB after /theme <prefix> completes the theme names."""
    comp = cli._make_completer("http://127.0.0.1:1")  # never reached for themes
    assert comp("/theme ") == ["gold", "matrix", "ocean"]
    assert comp("/theme m") == ["matrix"]
    assert comp("/theme z") == []


def test_completer_offers_slash_commands() -> None:
    comp = cli._make_completer("http://127.0.0.1:1")
    hits = comp("/")
    for want in ("/exit", "/help", "/new", "/theme", "/tools", "/multi"):
        assert want in hits
    assert comp("/to") == ["/tools"]


# ----------------------------------------------------------------- history
def test_history_up_and_down(tmp_path: Path) -> None:
    hist = tmp_path / "h.txt"
    hist.write_text("first\nsecond\n", encoding="utf-8")
    ed = cli.LineEditor(hist)
    assert ed.read("❯ ", color=False, keys=["UP", "ENTER"]) == "second"
    assert ed.read("❯ ", color=False, keys=["UP", "UP", "ENTER"]) == "first"
    # DOWN past the newest entry restores the in-progress draft
    out = ed.read("❯ ", color=False,
                  keys=["a", "b", "UP", "UP", "DOWN", "DOWN", "ENTER"])
    assert out == "ab"


def test_history_remembers_and_persists(tmp_path: Path) -> None:
    hist = tmp_path / "h.txt"
    ed = cli.LineEditor(hist)
    assert ed.read("❯ ", color=False, keys=["h", "i", "ENTER"]) == "hi"
    assert ed.history == ["hi"]
    assert hist.read_text(encoding="utf-8").strip() == "hi"
    # a fresh editor instance sees the persisted history
    ed2 = cli.LineEditor(hist)
    assert ed2.read("❯ ", color=False, keys=["UP", "ENTER"]) == "hi"


def test_history_dedupes_consecutive_duplicates(tmp_path: Path) -> None:
    hist = tmp_path / "h.txt"
    ed = cli.LineEditor(hist)
    ed.read("❯ ", color=False, keys=["a", "ENTER"])
    ed.read("❯ ", color=False, keys=["a", "ENTER"])
    assert ed.history == ["a"]


def test_history_capped(tmp_path: Path) -> None:
    hist = tmp_path / "h.txt"
    ed = cli.LineEditor(hist)
    for i in range(600):
        ed.remember(f"line-{i}")
    assert len(ed.history) <= cli.HISTORY_MAX


# ----------------------------------------------------------------- editing
def test_backspace(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    assert ed.read("❯ ", color=False, keys=["a", "b", "c", "BS", "ENTER"]) == "ab"


def test_left_insert(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    out = ed.read("❯ ", color=False,
                  keys=["a", "b", "LEFT", "LEFT", "X", "ENTER"])
    assert out == "Xab"


def test_home_end(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    out = ed.read("❯ ", color=False,
                  keys=["a", "b", "HOME", "Z", "END", "!", "ENTER"])
    assert out == "Zab!"


def test_delete_forward(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    out = ed.read("❯ ", color=False,
                  keys=["a", "b", "c", "LEFT", "LEFT", "DEL", "ENTER"])
    assert out == "ac"


def test_cancel_returns_none(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    assert ed.read("❯ ", color=False, keys=["a", "b", "CANCEL"]) is None


# ----------------------------------------------------------------- multi-line
def test_paste_burst_opens_multiline(tmp_path: Path) -> None:
    """A pasted code block (clipboard content ends with a newline) opens
    continuation lines and a single Enter submits the whole block."""
    ed = cli.LineEditor(tmp_path / "h.txt")
    paste = "def f():\n    return 1\n\nprint(f())\n"  # trailing \n, like a real paste
    out = ed.read("❯ ", color=False, keys=[paste, "ENTER"])
    assert out == paste.rstrip("\n")


def test_paste_without_trailing_newline_needs_two_enters(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    paste = "def f():\n    return 1"  # no trailing newline in the clipboard
    out = ed.read("❯ ", color=False, keys=[paste, "ENTER", "ENTER"])
    assert out == paste


def test_typed_continuation_with_backslash(tmp_path: Path) -> None:
    """A trailing backslash (bash-style) opens a typed continuation line."""
    BS = chr(92)  # shell-proof way to spell a single backslash
    ed = cli.LineEditor(tmp_path / "h.txt")
    out = ed.read("❯ ", color=False,
                  keys=["line one " + BS, "ENTER", "line two", "ENTER", "ENTER"])
    assert out == "line one \nline two"


def test_single_line_paste_submits_directly(tmp_path: Path) -> None:
    ed = cli.LineEditor(tmp_path / "h.txt")
    out = ed.read("❯ ", color=False, keys=["quick note", "ENTER"])
    assert out == "quick note"


# ----------------------------------------------------------------- /tools
def test_tool_specs_lists_all_core_tools() -> None:
    """The server-side catalogue must cover every tool the agent can call."""
    sys.path.insert(0, str(ROOT / "file-agent"))
    import os as _os

    _os.environ.setdefault("AGENT_WORKSPACE", "D:/hwk-projects")
    from agent_loop import tool_specs

    specs = tool_specs()
    names = [s["name"] for s in specs]
    for want in ("write_file", "read_file", "run_command", "read_image",
                 "analyze_video", "make_n8n_workflow"):
        assert want in names
    assert all(s["description"].strip() for s in specs)
    assert len(specs) >= 10
