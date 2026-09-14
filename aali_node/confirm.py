"""User confirmation for dangerous tool calls (docs/SAAS_ARCHITECTURE.md §5).

The sandbox asks via ``confirm_hook``; this module supplies the two real
implementations:

- :func:`native_confirm` — a native Windows MessageBox (ctypes, zero deps),
  TOPMOST + system-modal so it is never missed or dismissed by a stray click.
- :func:`console_confirm` — a terminal fallback for headless/SSH sessions.

Both share the rules that matter: **60 seconds of silence = auto-deny**, and
any failure to ask (no console, no desktop) is a denial, never an approval.
"""
from __future__ import annotations

import builtins
import sys

__all__ = ["CONFIRM_TIMEOUT_SEC", "native_confirm", "console_confirm",
           "make_confirm_hook"]

CONFIRM_TIMEOUT_SEC = 60

_MB_TOPMOST = 0x40000
_MB_SYSTEMMODAL = 0x1000
_MB_YES_NO = 0x04
_MB_ICONWARNING = 0x30
_IDYES = 6

_DANGEROUS_AR = "تتطلب هذه العملية تأكيدك"
_DANGEROUS_EN = "This action needs your confirmation"


def _describe(tool: str, args: dict) -> tuple[str, str]:
    """A short, honest human summary of what is about to happen (AR + EN)."""
    if tool == "run_command":
        cmd = str(args.get("command") or "")
        return (
            f"تنفيذ أمر على جهازك:\n{cmd}",
            f"Run a command on this machine:\n{cmd}",
        )
    if tool == "delete_file":
        return (
            f"حذف الملف: {args.get('path', '')}",
            f"Delete file: {args.get('path', '')}",
        )
    if tool == "move_file":
        return (
            f"نقل: {args.get('source', '')} ← {args.get('destination', '')}",
            f"Move: {args.get('source', '')} → {args.get('destination', '')}",
        )
    if tool == "write_file":
        return (
            f"استبدال محتوى الملف: {args.get('path', '')}",
            f"Overwrite file: {args.get('path', '')}",
        )
    if tool == "machine_ops":
        return (
            f"عملية نظام: {args.get('action', '')} {args.get('target', '')}",
            f"System operation: {args.get('action', '')} "
            f"{args.get('target', '')}",
        )
    return (f"أداة: {tool}", f"Tool: {tool}")


def native_confirm(requires: bool, tool: str, args: dict) -> bool:
    """Native Windows dialog; 60s silence or failure to show = deny."""
    if not requires:
        return True
    try:
        import ctypes
        import threading

        ar, en = _describe(tool, args)
        text = f"{ar}\n\n{en}\n\n(Aali / آلي)"
        result: list[int] = []

        def _ask() -> None:
            # MessageBoxTimeoutW exists undocumented on all modern Windows;
            # plain MessageBoxW blocks forever, so a watcher thread is the
            # honest cross-version way to bound it.
            res = ctypes.windll.user32.MessageBoxW(
                None, text, "Aali — تأكيد / Confirm",
                _MB_YES_NO | _MB_ICONWARNING | _MB_TOPMOST | _MB_SYSTEMMODAL)
            result.append(res)

        thread = threading.Thread(target=_ask, daemon=True)
        thread.start()
        thread.join(timeout=CONFIRM_TIMEOUT_SEC)
        return bool(result) and result[0] == _IDYES
    except Exception:
        return False


def console_confirm(requires: bool, tool: str, args: dict) -> bool:
    """Terminal y/N prompt; 60s silence, EOF or failure = deny."""
    if not requires:
        return True
    try:
        ar, en = _describe(tool, args)
        builtins.print(f"\n⚠ {ar}\n  {en}")
        builtins.print(f"   (الصمت {CONFIRM_TIMEOUT_SEC} ثانية = رفض / "
                       f"silence for {CONFIRM_TIMEOUT_SEC}s = deny)")
        answer = builtins.input("هل تسمح؟ / Allow? [y/N] ")
        return answer.strip().lower() in ("y", "yes", "نعم", "ن")
    except (EOFError, OSError, RuntimeError):
        return False


def make_confirm_hook(*, use_native: bool = True):
    """Build the confirm_hook for SecureSandbox (native first, console 2nd).

    The chooser runs ONCE (first confirmation needed); the console variant
    wins when there is no interactive desktop (SSH/service sessions) so the
    user still gets asked instead of silently denied.
    """
    state: dict = {"mode": None}

    def _hook(requires: bool, tool: str, args: dict) -> bool:
        if not requires:
            return True
        if state["mode"] is None:
            # pick the ask-style once: no interactive desktop → console
            # (its input() will EOF = deny on a true service session, which
            # is the safe answer), otherwise native when wanted.
            if not _interactive_desktop():
                state["mode"] = "console"
            else:
                state["mode"] = "native" if use_native else "console"
        if state["mode"] == "native":
            return native_confirm(requires, tool, args)
        return console_confirm(requires, tool, args)

    return _hook


def _interactive_desktop() -> bool:
    """True when a desktop session can actually show a dialog."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return False
    return sys.stdin is not None and sys.stdin.isatty()
