"""User confirmation for dangerous tool calls (docs/SAAS_ARCHITECTURE.md §5).

The sandbox asks via ``confirm_hook``; this module supplies the two real
implementations:

- :func:`native_confirm` — a custom Aali-styled Windows dialog built from an
  in-memory DLGTEMPLATE (ctypes, zero deps; tkinter stays excluded from the
  Node build). Warm black + gold, the house palette of the shell and the
  first-run config screen; Arabic headline, English body, and a live 60s
  countdown. If the custom dialog cannot show, it falls back to the classic
  system MessageBoxW — and ANY failure to ask is a denial, never an approval.
- :func:`console_confirm` — a terminal fallback for headless/SSH sessions.

Both share the rules that matter: **60 seconds of silence = auto-deny**, and
any failure to ask (no console, no desktop) is a denial, never an approval.

The dialog's input contract is deliberately safe-first:

- the No button is styled as the primary (gold) and takes initial focus;
- **Enter always denies** (no default-button approval is possible — a click,
  or Tab+Space on Yes, is required to allow);
- Esc and the caption ✕ deny;
- the window is topmost and centered on the foreground monitor, so it is
  never missed;
- mirrored (RTL layout) when the user's UI language is Arabic.
"""
from __future__ import annotations

import builtins
import sys

__all__ = ["CONFIRM_TIMEOUT_SEC", "native_confirm", "console_confirm",
           "make_confirm_hook"]

CONFIRM_TIMEOUT_SEC = 60

_IDYES = 6
_IDNO = 7

_DANGEROUS_AR = "تتطلب هذه العملية تأكيدك"
_DANGEROUS_EN = "This action needs your confirmation"

_TITLE = "آلي — تأكيد / Aali — Confirm"


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


# ---------------------------------------------------------------------------
# The custom dialog — pure Win32 via ctypes (no frameworks, nothing to bundle)
# ---------------------------------------------------------------------------

# DLGTEMPLATE dialog styles
_DS_MODALFRAME = 0x00000080
_DS_SETFONT = 0x00000040
_DS_CENTER = 0x00000800
_WS_POPUP = 0x80000000
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_TABSTOP = 0x00010000
_BS_OWNERDRAW = 0x0000000B

_WS_EX_TOPMOST = 0x00000008
_WS_EX_RTLREADING = 0x00001000
_WS_EX_LAYOUTRTL = 0x00002000

# class atoms for DLGITEMTEMPLATE
_CLS_BUTTON = 0x0080

# window messages
_WM_INITDIALOG = 0x0110
_WM_PAINT = 0x000F
_WM_ERASEBKGND = 0x0014
_WM_CTLCOLORDLG = 0x0136
_WM_COMMAND = 0x0111
_WM_CLOSE = 0x0010
_WM_DESTROY = 0x0002
_WM_TIMER = 0x0113
_WM_DRAWITEM = 0x002B

_ODT_BUTTON = 4
_ODS_SELECTED = 0x1

# DrawTextW flags
_DT_CENTER = 0x0001
_DT_VCENTER = 0x0004
_DT_SINGLELINE = 0x0020
_DT_WORDBREAK = 0x0010
_DT_NOPREFIX = 0x0800
_DT_ENDELLIPSIS = 0x8000

# dialog geometry, in dialog units (GetDialogBaseUnits converts to px)
_DLG_CX = 252
_DLG_CY = 116
_BTN_CX = 70
_BTN_CY = 18
_BTN_MARGIN = 14
_BTN_BOTTOM_GAP = 12

# house palette — COLORREF is 0x00BBGGRR
_BG = 0x00232525        # #252523 warm black
_PANEL = 0x002A2D2D     # #2d2d2a panel
_LINE = 0x00363A3A      # #3a3a36 hairline
_GOLD = 0x004EA9D4      # #d4a94e gold
_GOLD_HI = 0x006AC0E8   # #e8c06a pressed gold
_TEXT = 0x00D8E4E8      # #e8e4d8 warm off-white
_DIM = 0x008A959A       # #9a958a dim warm gray
_RED = 0x005F6FC9       # #c96f5f clay red
_INK = 0x00232525       # dark ink on gold

_BTN_NO_LABEL = "لا / No"
_BTN_YES_LABEL = "نعم / Yes"


def _pack_dialog_template(title: str, rtl: bool) -> bytes:
    """An in-memory DLGTEMPLATE: the dialog + its two owner-draw buttons.

    The No button (id 7) is created FIRST so it is the first tabstop — the
    focus lands on the safe answer. Pure function, headless-testable.
    """
    buf = bytearray()

    def w(v: int) -> None:
        buf.extend(v.to_bytes(2, "little"))

    def d(v: int) -> None:
        buf.extend(v.to_bytes(4, "little"))

    def wz(s: str) -> None:
        buf.extend(s.encode("utf-16-le") + b"\0\0")

    def align4() -> None:
        while len(buf) % 4:
            buf.append(0)

    style = (_WS_POPUP | _WS_CAPTION | _WS_SYSMENU | _DS_MODALFRAME
             | _DS_CENTER | _DS_SETFONT)
    ex = _WS_EX_TOPMOST | ((_WS_EX_RTLREADING | _WS_EX_LAYOUTRTL) if rtl
                           else 0)
    d(style)
    d(ex)
    w(2)                       # cdit — two buttons
    w(0); w(0)                 # x, y (DS_CENTER decides)
    w(_DLG_CX); w(_DLG_CY)
    w(0); w(0)                 # menu = none, class = default
    wz(title)
    w(9)                       # 9pt
    wz("Segoe UI")

    def item(x: int, id_: int, text: str) -> None:
        align4()
        d(_WS_CHILD | _WS_VISIBLE | _WS_TABSTOP | _BS_OWNERDRAW)
        d(0)                   # item ex-style
        w(x); w(_DLG_CY - _BTN_BOTTOM_GAP - _BTN_CY)
        w(_BTN_CX); w(_BTN_CY)
        w(id_)
        w(0xFFFF); w(_CLS_BUTTON)
        wz(text)
        w(0)                   # no extra data

    # LTR layout: Yes on the right (primary-looking No just left of it);
    # a mirrored dialog (RTL) flips both automatically.
    yes_x = _DLG_CX - _BTN_MARGIN - _BTN_CX
    no_x = yes_x - 8 - _BTN_CX
    item(no_x, _IDNO, _BTN_NO_LABEL)
    item(yes_x, _IDYES, _BTN_YES_LABEL)
    return bytes(buf)


def _show_confirm_dialog(title: str, ar: str, en: str, timeout: int) -> int:
    """Run the styled Aali dialog; returns _IDYES / _IDNO (never blocks past
    `timeout` seconds — the dialog's own timer ends it with a denial).

    Raises on failure — the caller (native_confirm) turns any raise into the
    MessageBoxW fallback, and any fallback failure into a denial.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    DLGPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HWND, ctypes.c_uint,
        wintypes.WPARAM, wintypes.LPARAM)
    user32.DialogBoxIndirectParamW.restype = ctypes.c_int
    user32.DialogBoxIndirectParamW.argtypes = [
        wintypes.HINSTANCE, ctypes.c_void_p, wintypes.HWND, DLGPROC,
        wintypes.LPARAM]

    class DRAWITEMSTRUCT(ctypes.Structure):
        _fields_ = [
            ("CtlType", wintypes.UINT), ("CtlID", wintypes.UINT),
            ("itemID", wintypes.UINT), ("itemAction", wintypes.UINT),
            ("itemState", wintypes.UINT), ("hwndItem", wintypes.HWND),
            ("hDC", wintypes.HDC), ("rcItem", wintypes.RECT),
            ("itemData", ctypes.c_size_t)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    # mirrored layout when the user's primary UI language is Arabic
    rtl = False
    try:
        rtl = (kernel32.GetUserDefaultUILanguage() & 0xFF) == 0x01
    except Exception:
        rtl = False

    # crisp text on high-DPI screens; harmless no-op if the process is
    # already DPI-aware (python.exe's manifest usually is)
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    hbr_bg = gdi32.CreateSolidBrush(_BG)
    hbr_panel = gdi32.CreateSolidBrush(_PANEL)
    hbr_line = gdi32.CreateSolidBrush(_LINE)
    hbr_gold = gdi32.CreateSolidBrush(_GOLD)
    hbr_gold_hi = gdi32.CreateSolidBrush(_GOLD_HI)

    def _font(px: int, weight: int) -> int:
        return gdi32.CreateFontW(
            -px, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")

    font_head = _font(15, 700)
    font_body = _font(14, 400)
    font_glyph = _font(34, 700)
    font_btn = _font(13, 600)

    elapsed = [0]

    def _text(hdc, s: str, rect, flags: int) -> None:
        user32.DrawTextW(hdc, s, -1, ctypes.byref(rect), flags)

    def _paint_body(hdc, W: int, H: int) -> None:
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
        m = max(16, int(W * 0.055))
        gs = max(36, int(H * 0.22))

        # gold warning triangle with a clay-red "!"
        pen = gdi32.CreatePenW(0, 2, _GOLD)
        old_pen = gdi32.SelectObject(hdc, pen)
        gdi32.MoveToEx(hdc, m, m + gs, None)
        gdi32.LineTo(hdc, m + gs // 2, m)
        gdi32.LineTo(hdc, m + gs, m + gs)
        gdi32.LineTo(hdc, m, m + gs)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(pen)
        gdi32.SetTextColor(hdc, _RED)
        gdi32.SelectObject(hdc, font_glyph)
        _text(hdc, "!", wintypes.RECT(m, m, m + gs, m + gs),
              _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE | _DT_NOPREFIX)

        # headline: the AR summary (bold gold) beside the glyph
        tx = m + gs + 14
        gdi32.SetTextColor(hdc, _GOLD)
        gdi32.SelectObject(hdc, font_head)
        head = ar.split("\n", 1)[0]
        _text(hdc, head,
              wintypes.RECT(tx, m, W - m, m + gs + 6),
              _DT_WORDBREAK | _DT_ENDELLIPSIS | _DT_NOPREFIX)

        # body: rest of the AR summary + the EN line
        gdi32.SetTextColor(hdc, _TEXT)
        gdi32.SelectObject(hdc, font_body)
        rest = ar.split("\n", 1)[1]
        body = "\n".join(part for part in (rest, en) if part)
        _text(hdc, body,
              wintypes.RECT(m, m + gs + 16, W - m, int(H * 0.70)),
              _DT_WORDBREAK | _DT_ENDELLIPSIS | _DT_NOPREFIX)

        # hairline + live countdown above the buttons
        hy = int(H * 0.815)
        gdi32.FillRect(hdc, ctypes.byref(wintypes.RECT(0, hy, W, hy + 1)),
                       hbr_line)
        gdi32.SetTextColor(hdc, _DIM)
        left = max(0, timeout - elapsed[0])
        _text(hdc, f"رفض تلقائي بعد {left} ثانية · auto-deny in {left}s",
              wintypes.RECT(m, int(H * 0.84), W - m, int(H * 0.93)),
              _DT_NOPREFIX)

    def _paint_button(hdc, rc: wintypes.RECT, yes: bool,
                      selected: bool, focused: bool = False) -> None:
        if yes:
            fill = hbr_gold_hi if selected else hbr_gold
            border, label, ink = _RED, _BTN_YES_LABEL, _INK
        else:
            fill = hbr_line if selected else hbr_panel
            border, label, ink = _GOLD, _BTN_NO_LABEL, _TEXT
        pen = gdi32.CreatePenW(0, 2, border)
        old_pen = gdi32.SelectObject(hdc, pen)
        old_br = gdi32.SelectObject(hdc, fill)
        gdi32.RoundRect(hdc, rc.left, rc.top, rc.right, rc.bottom, 18, 18)
        gdi32.SelectObject(hdc, old_br)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(pen)
        gdi32.SetTextColor(hdc, ink)
        gdi32.SelectObject(hdc, font_btn)
        gdi32.SetBkMode(hdc, 1)
        _text(hdc, label, rc,
              _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE
              | _DT_NOPREFIX | _DT_ENDELLIPSIS)
        if focused:
            user32.DrawFocusRect(hdc, ctypes.byref(
                wintypes.RECT(rc.left + 3, rc.top + 3,
                              rc.right - 3, rc.bottom - 3)))

    def _center_over_foreground(hwnd) -> None:
        try:
            mon = user32.MonitorFromWindow(
                user32.GetForegroundWindow(), 2)  # MONITOR_DEFAULTTONEAREST
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(mi)
            if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return
            wr = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
                return
            wd, ht = wr.right - wr.left, wr.bottom - wr.top
            wa = mi.rcWork
            user32.SetWindowPos(
                hwnd, None,
                wa.left + ((wa.right - wa.left) - wd) // 2,
                wa.top + ((wa.bottom - wa.top) - ht) // 2,
                0, 0, 0x0005)  # SWP_NOSIZE | SWP_NOZORDER
        except Exception:
            pass

    def _proc(hwnd, msg, wp, lp) -> int:
        if msg == _WM_INITDIALOG:
            _center_over_foreground(hwnd)
            user32.SetTimer(hwnd, 1, 1000, None)
            # safe answer takes focus first
            user32.SetFocus(user32.GetDlgItem(hwnd, _IDNO))
            return 0
        if msg == _WM_TIMER:
            elapsed[0] += 1
            if elapsed[0] >= timeout:
                user32.EndDialog(hwnd, _IDNO)   # silence = deny
            else:
                user32.InvalidateRect(hwnd, None, False)
            return 1
        if msg == _WM_COMMAND:
            cid = wp & 0xFFFF
            if cid in (_IDYES, _IDNO):
                user32.EndDialog(hwnd, cid)     # real button click
            else:                               # Enter (IDOK) / Esc (IDCANCEL)
                user32.EndDialog(hwnd, _IDNO)   # keyboard default = deny
            return 1
        if msg == _WM_CLOSE:                    # caption ✕
            user32.EndDialog(hwnd, _IDNO)
            return 1
        if msg == _WM_DESTROY:
            user32.KillTimer(hwnd, 1)
            return 0
        if msg == _WM_ERASEBKGND:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            gdi32.FillRect(wintypes.HDC(wp), ctypes.byref(rc), hbr_bg)
            return 1
        if msg == _WM_CTLCOLORDLG:
            return hbr_bg
        if msg == _WM_PAINT:
            ps = wintypes.PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                rc = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rc))
                _paint_body(hdc, rc.right, rc.bottom)
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 1
        if msg == _WM_DRAWITEM:
            dis = ctypes.cast(lp, ctypes.POINTER(DRAWITEMSTRUCT)).contents
            if dis.CtlType == _ODT_BUTTON:
                _paint_button(dis.hDC, dis.rcItem,
                              dis.CtlID == _IDYES,
                              bool(dis.itemState & _ODS_SELECTED),
                              bool(dis.itemState & _ODS_FOCUS))
                return 1
        return 0

    proc = DLGPROC(_proc)
    template = ctypes.create_string_buffer(
        _pack_dialog_template(title, rtl))
    try:
        result = user32.DialogBoxIndirectParamW(
            kernel32.GetModuleHandleW(None), template, None, proc, 0)
    finally:
        for handle in (hbr_bg, hbr_panel, hbr_line, hbr_gold, hbr_gold_hi,
                       font_head, font_body, font_glyph, font_btn):
            if handle:
                gdi32.DeleteObject(handle)
    if result == -1:
        raise OSError("DialogBoxIndirectParamW failed")
    return result


def native_confirm(requires: bool, tool: str, args: dict) -> bool:
    """Aali-styled dialog; 60s silence or failure to show = deny.

    Tries the custom dialog first; if it cannot show, falls back to the
    classic system MessageBox; if even that fails, the answer is a denial —
    a broken ask is never an approval.
    """
    if not requires:
        return True
    ar, en = _describe(tool, args)
    if sys.platform.startswith("win"):
        try:
            return _show_confirm_dialog(
                _TITLE, ar, en, CONFIRM_TIMEOUT_SEC) == _IDYES
        except Exception:
            pass
    try:
        return _fallback_messagebox(_TITLE, ar, en) == _IDYES
    except Exception:
        return False


def _fallback_messagebox(title: str, ar: str, en: str) -> int:
    """Classic MessageBoxW with the 60s watcher-thread bound."""
    import ctypes
    import threading

    _MB_TOPMOST = 0x40000
    _MB_SYSTEMMODAL = 0x1000
    _MB_YES_NO = 0x04
    _MB_ICONWARNING = 0x30

    text = f"{ar}\n\n{en}\n\n(Aali / آلي)"
    result: list[int] = []

    def _ask() -> None:
        try:
            res = ctypes.windll.user32.MessageBoxW(
                None, text, title,
                _MB_YES_NO | _MB_ICONWARNING | _MB_TOPMOST
                | _MB_SYSTEMMODAL)
            result.append(res)
        except Exception:
            pass  # no answer = the caller's empty `result` = deny

    thread = threading.Thread(target=_ask, daemon=True)
    thread.start()
    thread.join(timeout=CONFIRM_TIMEOUT_SEC)
    return result[0] if result else _IDNO


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
