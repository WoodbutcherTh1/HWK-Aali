#!/usr/bin/env python3
"""Mission clock - a colorful watch-party dashboard for Aali's long jobs.

The owner asked for something fun to stare at while the GPU cooks: moving
clocks, the processes that are pending, how far along they are and how much
time is left. Every number on screen is parsed from the SAME logs the agents
use (soup_pipeline.log, soup_train_live.log, soup_salvage.log,
context_training.log, resft_pipeline_report.md) - nothing is guessed.

stdlib only; Windows Terminal-first (VT via os.system('') plus a
SetConsoleMode fallback for classic conhost). Ctrl+C restores the cursor and
prints a one-line status.

Usage:
  python scripts/mission_clock.py                 # live, ~1s refresh
  python scripts/mission_clock.py --once          # single frame (tests/CI)
  python scripts/mission_clock.py --interval 2 --plain --data-dir D:/hwk-data
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# ANSI palette (Windows Terminal + conhost both honor these once VT is on)
# --------------------------------------------------------------------------

if os.name == "nt":
    os.system("")  # the documented trick: enables VT processing in conhost
    try:  # belt and suspenders for older conhost builds
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass

BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
CYAN, MAGENTA, YELLOW, GREEN, RED, GRAY = (
    "\x1b[36m", "\x1b[35m", "\x1b[33m", "\x1b[32m", "\x1b[31m", "\x1b[90m")
RAINBOW = [MAGENTA, CYAN, GREEN, YELLOW]
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

PHASE_B_TARGET = 100_000
PHASE_C_TARGET = 2_800
PHASE_C_LOG = "phase_c_sft.log"
PHASE_C_CHAIN_LOG = "phase_c_chain.log"
DEFAULT_DATA_DIR = Path("D:/hwk-data")
PROMOTED_PORT = 20129
AALI_API_PORT = 5055


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if text else text


# --------------------------------------------------------------------------
# 5-row blocky digits for the big moving clock
# --------------------------------------------------------------------------

GLYPHS: dict[str, tuple[str, str, str, str, str]] = {
    "0": ("███", "█ █", "█ █", "█ █", "███"),
    "1": ("  █", "  █", "  █", "  █", "  █"),
    "2": ("███", "  █", "███", "█  ", "███"),
    "3": ("███", "  █", "███", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"),
    "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"),
    "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"),
    "9": ("███", "█ █", "███", "  █", "███"),
    ":": ("   ", " █ ", "   ", " █ ", "   "),
    " ": ("   ", "   ", "   ", "   ", "   "),
}


def big_clock(now: datetime, blink: bool) -> list[str]:
    """Five rows of blocky digits showing HH:MM:SS (colons blink)."""
    stamp = now.strftime("%H:%M:%S")
    if blink:
        stamp = stamp.replace(":", " ")
    rows = ["", "", "", "", ""]
    for i, ch in enumerate(stamp):
        glyph = GLYPHS.get(ch, GLYPHS[" "])
        for r in range(5):
            rows[r] += glyph[r] + "  "
    return rows


# --------------------------------------------------------------------------
# Parsers - pure functions on text, each with a real-log sample test
# --------------------------------------------------------------------------

_TQDM_RE = re.compile(
    r"(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[(\d+:[\d:]+)<(\d+:[\d:]+),\s*([\d.]+)(s/it|it/s)\]")
_LOSS_RE = re.compile(r"\{'loss':\s*'([\d.]+)'")
_EXAM_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+)")
_STEP_RE = re.compile(r"step=(\d+)")
_TOKS_RE = re.compile(r"tok/s=([\d,]+)")
_STAMP_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")


def _hms_to_s(text: str) -> int:
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def fmt_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_tqdm(text: str) -> dict | None:
    """The LAST tqdm progress line of a trainer log.

    Sample (soup_train_live.log):
      77%|███████▋ | 2927/3804 [3:16:44<1:02:28, 4.27s/it]
    """
    match = None
    for match in _TQDM_RE.finditer(text):
        pass
    if match is None:
        return None
    pct, step, total, elapsed, eta, rate, unit = match.groups()
    eta_s = _hms_to_s(eta) if unit == "s/it" else None
    return {
        "pct": int(pct), "step": int(step), "total": int(total),
        "elapsed_s": _hms_to_s(elapsed), "eta_s": eta_s,
        "rate": float(rate), "unit": unit,
    }


def parse_loss(text: str) -> float | None:
    """Last {'loss': '0.6442', ...} dict line the HF trainer prints."""
    values = _LOSS_RE.findall(text)
    return float(values[-1]) if values else None


def parse_exam(text: str) -> tuple[int, int] | None:
    """Exam progress from the stage tail: last '[n/total] case' marker."""
    match = None
    for match in _EXAM_RE.finditer(text):
        pass
    return (int(match.group(1)), int(match.group(2))) if match else None


def parse_phase_b(text: str) -> dict:
    """Phase B trainer card data: latest step + tok/s from context_training.log."""
    step = None
    for match in _STEP_RE.finditer(text):
        step = int(match.group(1))
    toks = None
    for match in _TOKS_RE.finditer(text):
        toks = match.group(1)
    return {"step": step, "toks": toks,
            "done": "done:" in text or (step is not None and step >= PHASE_B_TARGET)}


def parse_stage_line(line: str) -> tuple[str, datetime] | None:
    """'[2026-09-12 13:29:13] message' (UTC stamp) -> (message, stamp)."""
    match = _STAMP_RE.match(line)
    if not match:
        return None
    return line[match.end():].strip(), datetime.strptime(
        match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def last_stage(text: str) -> tuple[str, float] | None:
    """(message, age_s) of the newest stamped, non-pytest line in a log."""
    now = time.time()
    for line in reversed(text.splitlines()):
        if "pytest-of" in line:
            continue
        parsed = parse_stage_line(line)
        if parsed is None:
            continue
        message, stamp = parsed
        return message, now - stamp.timestamp()
    return None


def verdict_from_report(text: str) -> str | None:
    """PROMOTE / NO-GO / INCOMPLETE from resft_pipeline_report.md."""
    if "**Verdict: PROMOTE" in text:
        return "PROMOTE"
    if "**Verdict: NO-GO" in text:
        return "NO-GO"
    if "**Verdict: INCOMPLETE" in text:
        return "INCOMPLETE"
    return None


def bar(frac: float, width: int = 24, frame: int = 0) -> str:
    """A gradient-colored progress bar; the fill hue cycles every few frames."""
    frac = min(1.0, max(0.0, frac))
    filled = round(frac * width)
    color = RAINBOW[(frame // 5) % len(RAINBOW)]
    cells = []
    for i in range(width):
        if i < filled - 1:
            cells.append(c("█", color))
        elif i == filled - 1 or (filled == 0 and frac > 0):
            cells.append(c("▓", color))
        else:
            cells.append(GRAY + "░" + RESET)
    return "[" + "".join(cells) + "]"


# --------------------------------------------------------------------------
# Cards: one per process someone might be waiting for
# --------------------------------------------------------------------------

@dataclass
class Card:
    title: str
    state: str = "idle"          # run | wait | done | stall | idle
    detail: str = ""
    frac: float | None = None
    eta_s: float | None = None
    extra: str = ""

    @property
    def icon(self) -> str:
        return {"run": "🔄", "wait": "⏳", "done": "✅",
                "stall": "⚠️ ", "idle": "💤"}.get(self.state, "…")

    @property
    def color(self) -> str:
        return {"run": CYAN, "wait": YELLOW, "done": GREEN,
                "stall": RED, "idle": GRAY}.get(self.state, GRAY)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _age(path: Path, now: float) -> float:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return float("inf")


def probe_model_port(port: int, timeout: float = 1.5) -> str | None:
    """/v1/models -> the served model id; None when the port is dead."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        entries = data.get("data") or []
        return str(entries[0]["id"]) if entries else "up"
    except Exception:  # noqa: BLE001 - a dead port is data, not a crash
        return None


def probe_gpu() -> dict | None:
    """One nvidia-smi snapshot (name, util %, VRAM, temp). None when
    nvidia-smi is missing or errors (Pi CI, iGPU-only boxes) - the card
    simply never renders there."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,"
                            "memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_nvidia_smi(out.splitlines()[0]) if out else None


def parse_nvidia_smi(line: str) -> dict | None:
    """One 'csv,noheader,nounits' row:
    'NVIDIA GeForce RTX 3070, 9, 7744, 8192, 48'
    (split from the right so GPU names containing commas survive)."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 5:
        return None
    name_parts, util, used, total, temp = parts[:-4], *parts[-4:]
    try:
        return {"name": ", ".join(name_parts), "util_pct": int(util),
                "vram_used_mib": int(used), "vram_total_mib": int(total),
                "temp_c": int(temp)}
    except ValueError:
        return None


def gpu_card(info: dict | None) -> Card | None:
    """The GPU card: bar = VRAM pressure, state from utilization.
    A loaded-but-idle card (the promoted soup server resident in VRAM)
    reads as 'wait', a busy one as 'run', a free one as 'idle'."""
    if info is None:
        return None
    used, total = info["vram_used_mib"], info["vram_total_mib"]
    frac = (used / total) if total else None
    gb = f"{used / 1024:.1f}/{total / 1024:.1f} GB"
    util, temp = info["util_pct"], info["temp_c"]
    if util >= 20:
        state, detail = "run", f"🔥 cooking - {util}% util - {gb}"
    elif frac is not None and frac >= 0.8:
        state, detail = "wait", f"model resident - {gb} - {util}% util"
    else:
        state, detail = "idle", f"idle - {util}% util - {gb} free"
    extra = f"{temp}°C" + (" 🥵" if temp >= 85 else "")
    return Card(title=f"GPU - {info['name']}", state=state,
                detail=detail, frac=frac, extra=extra)


def collect_cards(data_dir: Path, now: float | None = None,
                  brain_probe: Callable[[int], str | None] = probe_model_port,
                  gpu_probe: Callable[[], dict | None] | None = None,
                  ) -> list[Card]:
    """Build the card list from the real logs. brain_probe and gpu_probe are
    injectable so tests never touch the network or nvidia-smi (gpu_probe=None
    renders no GPU card at all)."""
    now = time.time() if now is None else now
    cards: list[Card] = []

    # 1. Aali's brains: the promoted soup adapter + the main API server
    served = brain_probe(PROMOTED_PORT)
    cards.append(Card(
        title="Aali's brain :20129 (soup adapter)",
        state="run" if served else "stall",
        detail=f"serving {served}" if served else "port DOWN",
        extra="LIVE" if served else "offline"))
    api = brain_probe(AALI_API_PORT)
    if api is not None:
        cards.append(Card(title="Aali API :5055", state="run",
                          detail="web/CLI/desktop brain", extra="LIVE"))

    # The star of the show: the 8GB card every long job fights over.
    gpu = gpu_card(gpu_probe()) if gpu_probe is not None else None
    if gpu is not None:
        cards.append(gpu)

    # 2. Graduation verdict (the thing the whole arc waits for)
    report = data_dir / "soup" / "resft_pipeline_report.md"
    verdict = verdict_from_report(_read(report))
    verdict_age = _age(report, now)
    if verdict and verdict_age < 48 * 3600:
        state = "done" if verdict == "PROMOTE" else "stall"
        icon = "🎉 PROMOTED" if verdict == "PROMOTE" else f"📜 {verdict}"
        cards.append(Card(
            title="Graduation verdict",
            state=state,
            detail=f"{icon} - {verdict} {fmt_hms(verdict_age)} ago",
            frac=1.0 if verdict == "PROMOTE" else 0.0))

    # 3. Soup re-SFT trainer (the long one - progress + THE countdown)
    live = data_dir / "soup_train_live.log"
    train_text = _read(live)
    train_age = _age(live, now)
    progress = parse_tqdm(train_text)
    if progress and train_age < 300:
        loss = parse_loss(train_text)
        cards.append(Card(
            title="Soup re-SFT trainer",
            state="run", frac=progress["pct"] / 100,
            eta_s=progress["eta_s"],
            detail=(f"step {progress['step']:,}/{progress['total']:,} "
                    f"({progress['pct']}%)"
                    + (f" - loss {loss}" if loss is not None else "")),
            extra=f"{progress['rate']}{progress['unit']}"))
    elif progress:
        cards.append(Card(
            title="Soup re-SFT trainer", state="stall",
            frac=progress["pct"] / 100, eta_s=progress["eta_s"],
            detail=f"stopped at step {progress['step']:,}/{progress['total']:,} "
                   f"(silent {fmt_hms(train_age)})"))

    # 4. Exam / pipeline stage (baseline, tuned, salvage)
    pipeline_text = _read(data_dir / "soup_pipeline.log")
    salvage_text = _read(data_dir / "soup_salvage.log")
    stage_src = salvage_text if _age(data_dir / "soup_salvage.log", now) < \
        _age(data_dir / "soup_pipeline.log", now) else pipeline_text
    stage = last_stage(stage_src)
    stage_age = min(_age(data_dir / "soup_pipeline.log", now),
                    _age(data_dir / "soup_salvage.log", now))
    if stage and stage_age < 6 * 3600:
        message, _ = stage
        if "pipeline start" in message:
            cards.append(Card(title="Graduation pipeline stage", state="run",
                              detail="just started - serving the teacher"))
        else:
            exam = parse_exam(_read(data_dir / "soup_pipeline_last_stage.txt"))
            frac = (exam[0] / exam[1]) if exam else None
            running = stage_age < 300
            cards.append(Card(
                title="Graduation pipeline stage",
                state="run" if running else "wait",
                frac=frac,
                detail=message[:70],
                extra=f"[{exam[0]}/{exam[1]}]" if exam else ""))
    elif not any(card.title == "Graduation verdict" for card in cards):
        cards.append(Card(title="Graduation pipeline", state="idle",
                          detail="no active stage"))

    # 5. Phase B trainer (done - the reason the arc started)
    phase = parse_phase_b(_read(data_dir / "context_training.log"))
    if phase["done"]:
        cards.append(Card(
            title="Phase B (context-4096)", state="done", frac=1.0,
            detail=f"COMPLETE - {phase['step']:,}/{PHASE_B_TARGET:,} steps"
                   + (f" - {phase['toks']} tok/s" if phase["toks"] else "")))
    elif phase["step"] is not None:
        cards.append(Card(
            title="Phase B (context-4096)", state="wait",
            frac=phase["step"] / PHASE_B_TARGET,
            detail=f"step {phase['step']:,}/{PHASE_B_TARGET:,}"))

    # 6. Night caretaker (sleeping is healthy)
    care_text = _read(data_dir / "caretaker.log")
    if care_text:
        lines = [l for l in care_text.splitlines() if l.strip()]
        if lines and "going to sleep" in lines[-1]:
            cards.append(Card(title="Night caretaker", state="idle",
                              detail="duty ended - sleeping 😴"))
        else:
            cards.append(Card(title="Night caretaker", state="wait",
                              detail="on duty"))

    # 7. Phase C: SFT of Aali's OWN from-zero brain (chain fires after v4)
    chain_text = _read(data_dir / PHASE_C_CHAIN_LOG)
    chain_lines = [l for l in (chain_text or "").splitlines() if l.strip()]
    chain_done = any("Phase C chain done" in l for l in chain_lines)
    chain_failed = any("FAILED" in l for l in chain_lines)
    if chain_done:
        cards.append(Card(title="Phase C - Aali's own brain", state="done",
                          frac=1.0, detail="SFT complete - review smoke output"))
    elif chain_failed:
        first_fail = next((l.split("] ", 1)[-1] for l in chain_lines
                           if "FAILED" in l), "failed")
        cards.append(Card(title="Phase C - Aali's own brain", state="stall",
                          detail=first_fail[:70]))
    else:
        phasec = parse_phase_b(_read(data_dir / PHASE_C_LOG) or "")
        if phasec["step"] is not None and phasec["step"] > 0:
            cards.append(Card(
                title="Phase C - Aali's own brain", state="run",
                frac=min(1.0, phasec["step"] / PHASE_C_TARGET),
                detail=f"SFT step {phasec['step']:,}/{PHASE_C_TARGET:,}"
                       + (f" - {phasec['toks']} tok/s" if phasec["toks"] else "")))
        elif chain_lines:
            waiting = chain_lines[-1].split("] ", 1)[-1]
            cards.append(Card(title="Phase C - Aali's own brain", state="wait",
                              detail=waiting[:70]))
        else:
            cards.append(Card(title="Phase C - Aali's own brain", state="idle",
                              detail="chain not started"))
    return cards


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _header(now: datetime, frame: int, width: int, promoted: bool) -> list[str]:
    blink = frame % 2 == 1
    digits = big_clock(now, blink)
    out: list[str] = []
    hue = RAINBOW[(frame // 10) % len(RAINBOW)]
    for r, row in enumerate(digits):
        if r == 0:
            stamp = now.strftime("%a %d %b %Y - UTC ")
            utc = now.astimezone(timezone.utc).strftime("%H:%M")
            meta = f"{stamp}{utc}"
            pad = max(0, width - len(row) - len(meta) - 4)
            out.append("  " + c(row, hue) + " " * pad + c(meta, DIM))
        else:
            side = "🎉 AALI GRADUATED 🎉" if (promoted and r == 2) else ""
            pad = max(0, width - len(row) - len(side) - 2)
            out.append("  " + c(row, hue) + (" " * pad + c(side, MAGENTA)))
    return out


def render(cards: list[Card], now: datetime, frame: int, width: int,
           plain: bool = False) -> str:
    """One full frame (without the clear escape) as a string. plain=True
    guarantees ZERO ANSI codes - the strip runs over the finished frame, so
    no helper can leak a color into pipes/tests."""
    def _c(text: str, color: str) -> str:
        return text if plain else c(text, color)

    promoted = any(card.state == "done" and "verdict" in card.title.lower()
                   for card in cards)
    lines = _header(now, frame, max(width, 80), promoted)
    lines.append("")
    lines.append(_c(f" ⚡ AALI MISSION CONTROL {frame * '·'}", BOLD + CYAN))
    lines.append("")

    next_event: tuple[str, float] | None = None
    for card in cards:
        spin = _c(SPINNER[frame % len(SPINNER)] if card.state == "run" else " ",
                  CYAN)
        title = _c(f"{card.icon} {card.title}", card.color)
        gauge = ""
        if card.frac is not None:
            gauge = bar(card.frac, width=20, frame=frame) + " "
        countdown = ""
        if card.eta_s is not None and card.state == "run":
            countdown = _c(f"⏳ ~{fmt_hms(card.eta_s)} left", YELLOW)
            if next_event is None or card.eta_s < next_event[1]:
                next_event = (card.title, card.eta_s)
        elif card.eta_s is not None:
            countdown = _c(f"(was ~{fmt_hms(card.eta_s)})", GRAY)
        row = f" {spin} {title}  {gauge}{_c(card.detail, card.color)}"
        if card.extra:
            row += _c(f"  {card.extra}", DIM)
        if countdown:
            row += "   " + countdown
        lines.append(row)  # cards are short; let the terminal wrap if needed

    lines.append("")
    if next_event is not None:
        title, eta = next_event
        lines.append(_c(f" ⏰ NEXT EVENT: {title} finishes in ~{fmt_hms(eta)}",
                        BOLD + YELLOW))
    else:
        lines.append(_c(" ⏰ nothing is counting down right now - all clear ✨",
                        GRAY))
    lines.append(_c(f" {now.strftime('%H:%M:%S')} - Ctrl+C to leave the "
                    f"watch party 👋", DIM))
    frame_text = "\n".join(lines)
    if plain:
        frame_text = re.sub(r"\x1b\[[0-9;]*m", "", frame_text)
    return frame_text


def main() -> int:
    # Block glyphs + emoji need UTF-8 out (bash pipes give cp1252 - the
    # same lesson soup_pipeline learned); errors=replace as a last resort.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Aali mission clock dashboard")
    parser.add_argument("--once", action="store_true", help="render one frame")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between frames (default 1)")
    parser.add_argument("--plain", action="store_true",
                        help="no colors/ANSI (for pipes and tests)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()

    interval = max(0.2, args.interval)
    frame = 0
    try:
        while True:
            now = datetime.now().astimezone()
            cards = collect_cards(args.data_dir, gpu_probe=probe_gpu)
            frame_text = render(cards, now, frame,
                                shutil.get_terminal_size().columns,
                                plain=args.plain)
            if args.once:
                print(frame_text)
                return 0
            sys.stdout.write("\x1b[2J\x1b[H" + frame_text + "\n")
            sys.stdout.flush()
            time.sleep(interval)
            frame += 1
    except KeyboardInterrupt:
        print(_c("\n 👋 watch party over - Aali keeps cooking in the "
                 "background.", CYAN))
        return 0
    finally:
        if not args.once:
            sys.stdout.write("\x1b[?25h")  # cursor back on
            sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
