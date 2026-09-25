"""Aali Monitor core (2026-09-25, owner-ordered side project).

A tiny desktop companion that watches Aali's long jobs from the REAL logs
in D:/hwk-data and raises a tray notification when something needs the
owner: a brain/API going down, a trainer dying, a NO-GO verdict, disk
pressure. Parsers are content-free (state + numbers only, never chat or
file content) and PURE — every function takes text/paths, so the suite
never touches the live machine.

This module must stay stdlib-only so it can run from .venv-monitor (and
degrade to any python) without dragging Flask/torch in.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

DATA_DIR = Path("D:/hwk-data")

SERVER_LOG = DATA_DIR / "aali_server.log"
PHASE_D_LOG = DATA_DIR / "phase_d_train.log"
PIPELINE_LOG = DATA_DIR / "soup_pipeline.log"
STATUS_MD = DATA_DIR / "STATUS.md"
MORNING_MD = DATA_DIR / "MORNING_REPORT.md"

DISK_MIN_FREE_GB = 20.0     # repo rule: never train under 20% free
API_URL = "http://127.0.0.1:5055/api/health"
BRAIN_URL = "http://127.0.0.1:20129/v1/models"


# ————————————————————————————————— log parsing —————————————————————————————————

def tail_lines(path: Path | str, n: int = 30) -> list[str]:
    """Last n lines of a file; [] when missing (never raises)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [ln.rstrip("\n") for ln in fh.readlines()[-max(1, n):]]
    except OSError:
        return []


def server_state(lines: list[str]) -> dict[str, Any]:
    """Aali API (:5055) liveness from aali_server.log.

    - "up"    : a health boot marker exists
    - "down"  : log ends with a traceback (the watchdog pattern)
    - "silent": log too old to prove anything (handled via freshness)
    """
    if not lines:
        return {"state": "unknown", "last_line_age_s": None}
    last = lines[-1].strip()
    boot = any("Running on" in ln or "Serving flask" in ln.lower()
               for ln in lines[-40:])
    crashed = bool(re.search(r"Traceback \(most recent call last\)", last)) \
        or last.startswith("[ERR]") or "Address already in use" in last
    if crashed:
        return {"state": "down", "last_error": last[:160]}
    if boot:
        return {"state": "up"}
    return {"state": "idle"}


def trainer_state(lines: list[str]) -> dict[str, Any]:
    """Phase D trainer: extract `step/total`, ETA hints, done marker.

    Understands the trainer's own tqdm-ish lines and the `done:` exit line
    (status_digest contract). Content-free: numbers and step text only.
    """
    out: dict[str, Any] = {"state": "unknown", "step": None, "total": None}
    for ln in reversed(lines):
        # `done:` with no \b after the colon — "done: eval=..." has a SPACE
        # there and \b would never match (caught by the suite, pinned here).
        if re.search(r"\bdone:\s", ln) or ln.rstrip().endswith("done:"):
            out["state"] = "done"
            mt = re.search(r"tokens?[= :]+([\d,.]+[KMB]?)", ln)
            if mt:
                out["tokens"] = mt.group(1)
            return out
        ms = re.search(r"(\d+)\s*/\s*(\d+)[ \t]*\[", ln)
        if ms:
            out["state"] = "running"
            out["step"], out["total"] = int(ms.group(1)), int(ms.group(2))
            return out
    return out


def pipeline_verdict(lines: list[str]) -> dict[str, Any]:
    """Graduation verdict from the pipeline log (write_verdict lines)."""
    for ln in reversed(lines):
        if "VERDICT" in ln.upper():
            up = ln.upper()
            if "PROMOTE" in up and "NO-GO" not in up and "NO GO" not in up:
                return {"verdict": "promote", "line": ln.strip()[:160]}
            if "NO-GO" in up or "NO GO" in up:
                return {"verdict": "no-go", "line": ln.strip()[:160]}
    return {"verdict": None}


def disk_free_gb(path: Path | str = "D:/") -> float | None:
    """Free GB on the drive holding `path` (shutil-free via ctypes when
    possible; falls back to os.statvfs-free approach on non-Windows)."""
    p = Path(path)
    try:
        import shutil
        total, used, free = shutil.disk_usage(p.anchor or str(p))
        return round(free / 1e9, 1)
    except OSError:
        return None


def gpu_probe(runner: Callable[..., subprocess.CompletedProcess]
              | None = None) -> dict[str, Any] | None:
    """nvidia-smi snapshot (same split-from-right parsing as the mission
    clock so comma GPU names survive). None = no card / no tool."""
    runner = runner or subprocess.run
    try:
        out = runner(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,"
                            "memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    parts = [p.strip() for p in out.splitlines()[0].split(",")]
    if len(parts) < 5:
        return None
    try:
        return {"name": ", ".join(parts[:-4]), "util_pct": int(parts[-4]),
                "vram_used_mib": int(parts[-3]), "vram_total_mib": int(parts[-2]),
                "temp_c": int(parts[-1])}
    except ValueError:
        return None


def http_ok(url: str, timeout: float = 2.0,
            opener: Callable[[str, float], int] | None = None) -> bool:
    """True when `url` answers HTTP 2xx. `opener` is injectable so tests
    never touch the network."""
    if opener is not None:
        try:
            return opener(url, timeout) == 200
        except Exception:  # noqa: BLE001
            return False
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        return False


# ————————————————————————————————— alerts —————————————————————————————————

def alerts(snapshot: dict[str, Any]) -> list[str]:
    """Human-readable (Arabic-first) alert lines for the tray. Pure."""
    out: list[str] = []
    if snapshot.get("api_up") is False:
        out.append("🚨 واجهة آلي (:5055) غير مستجيبة")
    if snapshot.get("brain_up") is False:
        out.append("🚨 عقل آلي (:20129) غير مستجيب")
    gpu = snapshot.get("gpu")
    if gpu and gpu.get("temp_c", 0) >= 85:
        out.append(f"🥨 حرارة الكرت {gpu['temp_c']}°C — تحقق من التهوية")
    tr = snapshot.get("trainer") or {}
    if tr.get("state") == "down":
        out.append("🚨 المدرب توقف بخطأ — راجع السجل")
    v = (snapshot.get("pipeline") or {}).get("verdict")
    if v == "no-go":
        out.append("❌ نتيجة التخرج: NO-GO — راجع تقرير التقييم")
    d = snapshot.get("disk_free_gb")
    if d is not None and d < DISK_MIN_FREE_GB:
        out.append(f"💾 المساحة الحرجة: {d} GB فقط على D:")
    return out


def snapshot(*, data_dir: Path | str | None = None,
             gpu_runner: Callable[..., subprocess.CompletedProcess] | None = None,
             api_opener: Callable[[str, float], int] | None = None,
             brain_opener: Callable[[str, float], int] | None = None) -> dict[str, Any]:
    """One content-free pass over the machine's real signals."""
    root = Path(data_dir) if data_dir else DATA_DIR
    snap: dict[str, Any] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    snap["api_up"] = http_ok(API_URL, opener=api_opener)
    snap["brain_up"] = http_ok(BRAIN_URL, opener=brain_opener)
    snap["server"] = server_state(tail_lines(root / "aali_server.log", 40))
    snap["trainer"] = trainer_state(tail_lines(root / "phase_d_train.log", 60))
    snap["pipeline"] = pipeline_verdict(tail_lines(root / "soup_pipeline.log", 60))
    snap["disk_free_gb"] = disk_free_gb("D:/")
    snap["gpu"] = gpu_probe(gpu_runner)
    snap["alert_lines"] = alerts(snap)
    return snap
