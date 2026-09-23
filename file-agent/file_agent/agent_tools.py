"""High-frequency agent tools missing from Aali's toolbox (2026-09-23).

Owner request: "check what Claude, DeepSeek, GLM and GPT agents have that
Aali doesn't — give him those tools and teach him how to use them right."

The gap set, chosen against what those agents ship and what Aali already
had (file CRUD, run_command, web, OCR/media, memory, machine_ops):

- create_artifact : shareable single-file HTML / Markdown output
- write_excel     : real .xlsx creation (openpyxl) — Aali could only READ docs
- read_excel      : .xlsx back into the conversation
- create_plot     : bar/line/pie chart PNGs — pure Pillow, no new deps, CPU
- diff_files      : unified diff preview BEFORE editing (evidence first)
- todo_plan       : persistent step list for multi-step work
- recall_search   : fetch a page, keep only the passages matching the query
- screenshot      : capture the screen the user asked about (confirm-gated,
                    guest-blocked — it is the owner's private screen)

Every tool stays inside the workspace jail (_resolve), every function
raises FileAgentError for expected failures, and heavy imports are lazy so
the training venv imports stay fast and lean.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from file_agent.file_tools import FileAgentError, _relative, _resolve, _write_text

PLAN_FILE = ".aali-plan.json"
MAX_ARTIFACT_CHARS = 500_000
MAX_PLAN_STEPS = 100
PLAN_STATUSES = ("pending", "in-progress", "done", "blocked")

# spawn_agents caps (2026-09-23): the brain serves one model on one 8GB card
# and every sub-agent is a full agent_loop — unbounded parallelism would
# queue GPU requests into timeouts and burn RAM on host-side history copies.
MAX_SUB_AGENTS = 4
MAX_SUB_AGENT_TURNS = 6        # short leash: sub-agents do ONE job each
SUB_AGENT_TIMEOUT_S = 300      # wall-clock cap per sub-agent
MAX_TASK_CHARS = 2000
_SPAWN_DEPTH = threading.local()  # recursion guard (per worker thread)


def _clean_stem(name: str, default: str) -> str:
    stem = re.sub(r"[^\w\u0600-\u06FF.-]+", "_", (name or "").strip()).strip("._")
    return stem[:80] or default


# ---------------------------------------------------------------------------
# artifacts — shareable single-file output (the Claude "artifact" pattern)
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: "Segoe UI", Tahoma, sans-serif; margin: 2rem auto;
         max-width: 860px; padding: 0 1rem; line-height: 1.7; }}
  h1,h2,h3 {{ line-height: 1.3; }}
  pre, code {{ font-family: Consolas, monospace; direction: ltr;
              text-align: left; }}
  pre {{ background: rgba(127,127,127,.12); padding: 1rem; border-radius: 8px;
        overflow-x: auto; }}
  table {{ border-collapse: collapse; }} td, th {{ border: 1px solid rgba(127,127,127,.4);
        padding: .35rem .6rem; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def create_artifact(title: str, content: str, workspace_root: str | Path, *,
                    artifact_type: str = "html", filename: str = "") -> dict[str, Any]:
    """Write a polished, shareable single-file artifact into the workspace.

    html: ``content`` may be a full document (written as-is) or a body
    fragment (wrapped in a minimal RTL-aware page with the title).
    markdown: written verbatim.
    """
    if not isinstance(content, str) or not content.strip():
        raise FileAgentError("content must be a non-empty string")
    if len(content) > MAX_ARTIFACT_CHARS:
        raise FileAgentError(f"content too large ({len(content)} chars; max {MAX_ARTIFACT_CHARS})")
    artifact_type = (artifact_type or "html").strip().lower()
    if artifact_type not in ("html", "markdown", "md"):
        raise FileAgentError("artifact_type must be html or markdown")
    ext = "html" if artifact_type == "html" else "md"
    name = _clean_stem(filename or title, "artifact") + "." + ext
    root, target = _resolve(name, workspace_root)
    if artifact_type == "html" and not re.search(r"<html[\s>]", content, re.I):
        from html import escape
        page = _HTML_TEMPLATE.format(title=escape(title or "نتيجة"),
                                     body=content)  # fragment: user markup kept
        text = page
    else:
        text = content
    _write_text(target, text, "utf-8")
    return {
        "path": _relative(root, target),
        "title": title,
        "type": ext,
        "bytes": target.stat().st_size,
        "hint": ("افتحه من العميل (web/desktop) أو شارك الملف — ملف واحد يعمل بلا إنترنت."
                 if ext == "html" else "Markdown file ready in the workspace."),
    }


# ---------------------------------------------------------------------------
# excel — real spreadsheets (Claude/GLM code-interpreter parity, no sandbox
# needed: openpyxl writes files, nothing executes)
# ---------------------------------------------------------------------------

def write_excel(path: str, rows: list[list[Any]], workspace_root: str | Path, *,
                sheet_name: str = "Sheet1", overwrite: bool = False,
                header: bool = True) -> dict[str, Any]:
    """Create an .xlsx file from rows (list of lists). First row = header."""
    if not isinstance(rows, list) or not rows:
        raise FileAgentError("rows must be a non-empty list of lists")
    if not str(path).lower().endswith(".xlsx"):
        path = str(path) + ".xlsx"
    root, target = _resolve(path, workspace_root)
    if target.exists() and not overwrite:
        raise FileAgentError(f"Destination already exists: {_relative(root, target)} (pass overwrite=true)")
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover
        raise FileAgentError("openpyxl unavailable") from exc
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Sheet1")[:31]
    for row in rows[:100_000]:
        ws.append([None if v is None or (isinstance(v, str) and v == "") else v
                   for v in (row if isinstance(row, list) else [row])])
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return {"path": _relative(root, target), "rows": min(len(rows), 100_000),
            "header": bool(header), "sheet": ws.title,
            "bytes": target.stat().st_size}


def read_excel(path: str, workspace_root: str | Path, *, sheet: str = "",
               max_rows: int = 200, max_cols: int = 40) -> dict[str, Any]:
    """Read an .xlsx file back as rows of values (evidence before answering)."""
    root, target = _resolve(path, workspace_root, must_exist=True)
    if target.suffix.lower() != ".xlsx":
        raise FileAgentError("read_excel expects an .xlsx file")
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise FileAgentError("openpyxl unavailable") from exc
    wb = load_workbook(target, read_only=True, data_only=True)
    title = sheet or wb.sheetnames[0]
    if title not in wb.sheetnames:
        raise FileAgentError(f"No sheet named {sheet!r}; sheets: {wb.sheetnames}")
    ws = wb[title]
    rows: list[list[Any]] = []
    for row in ws.iter_rows(max_row=max_rows, max_col=max_cols, values_only=True):
        vals = ["" if v is None else v for v in row]
        # openpyxl pads to max_col - trailing empties are padding, not data
        while vals and vals[-1] == "":
            vals.pop()
        rows.append(vals)
    wb.close()
    return {"path": _relative(root, target), "sheet": title,
            "sheets": wb.sheetnames[:20], "rows": rows,
            "truncated": ws.max_row > max_rows or ws.max_column > max_cols}


# ---------------------------------------------------------------------------
# plots — chart PNGs with pure Pillow (matplotlib is NOT in the training venv
# and heavy deps stay out; this draws axes/bars/lines/slices manually)
# ---------------------------------------------------------------------------

_PALETTE = [(234, 179, 8), (34, 197, 94), (56, 189, 248), (244, 63, 94),
            (168, 85, 247), (251, 146, 60), (20, 184, 166), (148, 163, 184)]


def _plot_font(size: int):
    try:
        from PIL import ImageFont
        for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001 - plotting must never hard-crash on fonts
        return None


def create_plot(data: dict[str, Any], output: str, workspace_root: str | Path, *,
                kind: str = "bar", title: str = "", x_label: str = "",
                y_label: str = "", width: int = 800, height: int = 500) -> dict[str, Any]:
    """Draw a bar / line / pie chart from {labels, series:[{name, values}]}."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover
        raise FileAgentError("Pillow unavailable") from exc
    kind = (kind or "bar").strip().lower()
    if kind not in ("bar", "line", "pie"):
        raise FileAgentError("kind must be bar, line or pie")
    labels = [str(x) for x in (data or {}).get("labels") or []]
    series = (data or {}).get("series") or []
    if not labels or not series or not isinstance(series, list):
        raise FileAgentError("data needs labels[] and series[{name, values}[]]")
    values = [[float(v) for v in (s.get("values") or [])] for s in series]
    names = [str(s.get("name") or f"series{i+1}") for i, s in enumerate(series)]
    if any(len(v) != len(labels) for v in values):
        raise FileAgentError("every series values[] must match labels[] length")
    if not any(any(v) for v in values):
        raise FileAgentError("all values are zero — nothing to draw")
    width, height = max(320, min(int(width), 2400)), max(240, min(int(height), 1800))

    img = Image.new("RGB", (width, height), (250, 250, 247))
    draw = ImageDraw.Draw(img)
    f_title, f_tick, f_leg = _plot_font(22), _plot_font(13), _plot_font(14)

    def _text(xy, s, font, fill=(40, 40, 40), anchor="la"):
        draw.text(xy, s, font=font, fill=fill, anchor=anchor)

    if title:
        _text((width // 2, 14), title, f_title, anchor="ma")

    def _legend(y):
        x = 18
        for i, name in enumerate(names[:8]):
            draw.rectangle([x, y, x + 14, y + 14], fill=_PALETTE[i % len(_PALETTE)])
            _text((x + 20, y - 2), name[:24], f_leg)
            x += 24 + int(draw.textlength(name[:24], font=f_leg)) + 18

    if kind == "pie":
        vals = values[0]
        total = sum(abs(v) for v in vals) or 1.0
        cx, cy, r = width // 2, height // 2 + 10, min(width, height) // 2 - 60
        start = -90.0
        for i, v in enumerate(vals):
            sweep = 360.0 * abs(v) / total
            draw.pieslice([cx - r, cy - r, cx + r, cy + r], start, start + sweep,
                          fill=_PALETTE[i % len(_PALETTE)], outline=(255, 255, 255), width=2)
            if sweep >= 12:
                mid = math.radians(start + sweep / 2)
                _text((cx + int(r * 0.65 * math.cos(mid)),
                       cy + int(r * 0.65 * math.sin(mid))),
                      f"{100 * abs(v) / total:.0f}%", f_tick, (30, 30, 30), "mm")
            start += sweep
        _legend(height - 34)
    else:
        left, top, right, bottom = 64, 56, width - 20, height - 64
        peak = max(max((abs(v) for v in col), default=1.0) for col in values) or 1.0
        # gridlines + ticks
        for g in range(5):
            y = bottom - (bottom - top) * g / 4
            draw.line([left, y, right, y], fill=(225, 222, 214))
            _text((left - 6, y), f"{peak * g / 4:.4g}".rstrip("0").rstrip("."), f_tick,
                  (110, 110, 110), "rm")
        draw.line([left, top, left, bottom], fill=(60, 60, 60))
        draw.line([left, bottom, right, bottom], fill=(60, 60, 60))
        n = len(labels)
        if kind == "bar":
            group_w = (right - left) / max(n, 1)
            bar_w = group_w * 0.72 / len(values)
            for ci, col in enumerate(values):
                for i, v in enumerate(col):
                    h = (bottom - top) * abs(v) / peak
                    x0 = left + i * group_w + group_w * 0.14 + ci * bar_w
                    draw.rectangle([x0, bottom - h, x0 + bar_w * 0.92, bottom],
                                   fill=_PALETTE[ci % len(_PALETTE)])
        else:  # line
            span_w = (right - left) / max(n - 1, 1) if n > 1 else 0
            for ci, col in enumerate(values):
                pts = [(left + i * span_w if n > 1 else (left + right) / 2,
                        bottom - (bottom - top) * abs(v) / peak)
                       for i, v in enumerate(col)]
                if len(pts) > 1:
                    draw.line(pts, fill=_PALETTE[ci % len(_PALETTE)], width=3)
                for (x, y) in pts:
                    draw.ellipse([x - 3, y - 3, x + 3, y + 3],
                                 fill=_PALETTE[ci % len(_PALETTE)])
        step = max(1, math.ceil(n / max(1, (right - left) // 70)))
        for i, lab in enumerate(labels):
            if i % step == 0:
                _text((left + (i + 0.5) * (right - left) / n if kind == "bar"
                       else left + i * span_w, bottom + 8), lab[:14], f_tick,
                      (90, 90, 90), "ma")
        if x_label:
            _text(((left + right) // 2, height - 12), x_label, f_tick, anchor="ma")
        if y_label:
            _text((12, (top + bottom) // 2), y_label[:24], f_tick, (90, 90, 90),
                  anchor="lm")
        _legend(top + 4 if not title else top + 6)
    root, target = _resolve(output if str(output).lower().endswith(".png")
                            else str(output) + ".png", workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, "PNG")
    return {"path": _relative(root, target), "kind": kind, "points": len(labels),
            "series": len(values), "bytes": target.stat().st_size,
            "note": "Pillow-drawn PNG (Arabic labels draw unshaped; keep labels short)"}


# ---------------------------------------------------------------------------
# diff — evidence before editing (preview, never guess)
# ---------------------------------------------------------------------------

def diff_files(path_a: str, path_b: str, workspace_root: str | Path, *,
               context: int = 3, max_lines: int = 400) -> dict[str, Any]:
    """Unified diff between two workspace files (or file vs nothing)."""
    root, ta = _resolve(path_a, workspace_root, must_exist=True)
    _, tb = _resolve(path_b, workspace_root, must_exist=True)
    a = ta.read_text(encoding="utf-8", errors="replace").splitlines()
    b = tb.read_text(encoding="utf-8", errors="replace").splitlines()
    lines = list(difflib.unified_diff(a, b, fromfile=_relative(root, ta),
                                      tofile=_relative(root, tb), lineterm="",
                                      n=max(0, min(int(context), 10))))
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return {"diff": "\n".join(lines[:max_lines]), "added": added, "removed": removed,
            "truncated": len(lines) > max_lines, "identical": not lines}


# ---------------------------------------------------------------------------
# todo_plan — persistent step list for multi-step work (the AGENTS.md /
# Freebuff-style plan, right inside the workspace)
# ---------------------------------------------------------------------------

def _plan_path(workspace_root: str | Path) -> Path:
    root, target = _resolve(PLAN_FILE, workspace_root)
    return target


def _plan_load(workspace_root: str | Path) -> list[dict[str, Any]]:
    path = _plan_path(workspace_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _plan_save(workspace_root: str | Path, steps: list[dict[str, Any]]) -> None:
    _write_text(_plan_path(workspace_root),
                json.dumps(steps, ensure_ascii=False, indent=1), "utf-8")


def todo_plan(action: str, workspace_root: str | Path, *, task: str = "",
              index: int | None = None, status: str = "") -> dict[str, Any]:
    """Track a multi-step task. add / list / update / complete / clear."""
    action = (action or "").strip().lower()
    steps = _plan_load(workspace_root)
    if action == "add":
        if not task.strip():
            raise FileAgentError("task must be a non-empty string")
        if len(steps) >= MAX_PLAN_STEPS:
            raise FileAgentError(f"plan already holds {MAX_PLAN_STEPS} steps — clear it first")
        steps.append({"task": task.strip()[:300], "status": "pending",
                      "added": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")})
        _plan_save(workspace_root, steps)
        return {"action": "add", "total": len(steps), "plan": steps}
    if action == "list":
        return {"action": "list", "total": len(steps), "plan": steps}
    if action == "update":
        if index is None or not (0 <= index < len(steps)):
            raise FileAgentError(f"index must be 0..{len(steps) - 1}")
        if status not in PLAN_STATUSES:
            raise FileAgentError(f"status must be one of {PLAN_STATUSES}")
        steps[index]["status"] = status
        _plan_save(workspace_root, steps)
        return {"action": "update", "total": len(steps), "plan": steps}
    if action == "complete":
        if index is None or not (0 <= index < len(steps)):
            raise FileAgentError(f"index must be 0..{len(steps) - 1}")
        steps[index]["status"] = "done"
        _plan_save(workspace_root, steps)
        return {"action": "complete", "total": len(steps), "plan": steps}
    if action == "clear":
        _plan_save(workspace_root, [])
        return {"action": "clear", "total": 0, "plan": []}
    raise FileAgentError("action must be add | list | update | complete | clear")


# ---------------------------------------------------------------------------
# recall_search — fetch a page, keep only what answers the query
# ---------------------------------------------------------------------------

def _page_text(url: str, workspace_root: str | Path) -> dict[str, Any]:
    """Fetch helper (injectable in tests — no network in the suite)."""
    from file_agent import web_tools as _web_tools
    return _web_tools.fetch_url(url, workspace_root)


def recall_search(url: str, query: str, workspace_root: str | Path, *,
                  max_chars: int = 3000, k: int = 3) -> dict[str, Any]:
    """Fetch a URL and return the k passages most relevant to the query.

    The 'right way' to use it: web_search first, then recall_search on the
    best URL — the answer cites only passages actually retrieved.
    """
    if not str(url).lower().startswith(("http://", "https://")):
        raise FileAgentError("url must start with http(s)://")
    if not query.strip():
        raise FileAgentError("query must be a non-empty string")
    page = _page_text(str(url), workspace_root)
    if isinstance(page, dict) and isinstance(page.get("result"), dict):
        page = page["result"]
    text = str(page.get("text") or page.get("content") or "")
    if not text.strip():
        raise FileAgentError(f"no readable text at {url}")
    terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1][:12]
    paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", text) if len(p.strip()) > 60]
    if not paras:
        paras = [text[i:i + 600] for i in range(0, len(text), 600)]
    scored = sorted(
        ((sum(p.lower().count(t) for t in terms), p) for p in paras),
        key=lambda pair: pair[0], reverse=True)
    top = [p for score, p in scored[:max(1, min(int(k), 6))] if score > 0] or \
          [scored[0][1]]
    joined, out = 0, []
    for p in top:
        piece = p[:max_chars - joined]
        if not piece:
            break
        out.append(piece)
        joined += len(piece) + 2
    return {"url": url, "query": query, "passages": out,
            "chars": joined, "source_chars": len(text),
            "caveat": "اقتباسات من الصفحة فقط — تحقق من التاريخ والمصدر قبل الجواب"}


# ---------------------------------------------------------------------------
# screenshot — capture the screen the user EXPLICITLY asked about
# ---------------------------------------------------------------------------

_PS_SCREENSHOT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
    "$g.Dispose();"
    "$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png);"
    "$bmp.Dispose()"
)


def screenshot(output: str, workspace_root: str | Path) -> dict[str, Any]:
    """Capture the whole virtual screen to a PNG inside the workspace.

    PRIVACY: this reads the owner's screen — it is confirm-gated in
    agent_loop (like print_file) and hard-blocked for remote guests. Only
    call it when the user explicitly asked ("شوف شاشتي" / "take a
    screenshot"), never proactively.
    """
    name = output if str(output).lower().endswith(".png") else str(output) + ".png"
    root, target = _resolve(name, workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    script = _PS_SCREENSHOT.format(path=str(target))
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise FileAgentError(f"screenshot failed to start: {exc}") from None
    if proc.returncode != 0 or not target.exists() or target.stat().st_size < 1000:
        raise FileAgentError(
            f"screenshot failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-200:]}")
    from PIL import Image  # local import: read dimensions for the reply
    with Image.open(target) as img:
        size = img.size
    return {"path": _relative(root, target), "width": size[0], "height": size[1],
            "bytes": target.stat().st_size,
            "note": "اقرأها بـ read_image لاستخراج النص — ولا تُظهر أسراراً تظهر فيها"}


# ---------------------------------------------------------------------------
# spawn_agents — parallel sub-agents (2026-09-23, owner request)
# ---------------------------------------------------------------------------
# "I want Aali to know how to run more than 1 agent at the same time if the
# user asks, or for a complex mission." Each sub-agent is a real agent_loop
# call with its own sandboxed workspace folder, short leash, and result
# contract. Cross-layer guards (already enforced, defense in depth):
#   - agent_loop._policy_gate still gates every tool the sub-agent calls
#   - run_command stays blocked for guests in app.py regardless of parent
#   - tool_guard validates names; sandbox resolves paths inside the jail

_SUB_AGENT_SYSTEM = (
    "You are a focused sub-agent. You were spawned by the main Aali agent to "
    "complete ONE task. Work only on that task, use your tools for real "
    "actions (files you create go to your own subfolder), never spawn more "
    "agents, and end with a clear, complete answer to the task. Reply in the "
    "task's language."
)
_SPAWN_ABSTRACT = (
    "أنا وحدة فرعية — لا أستطيع توليد وحدات أخرى (ممنوع التداخل)."
)


def _run_sub_agent(task: str, index: int, workspace_root: Path,
                   request_id: str | None, child_policy: str,
                   spawn_depth: int) -> dict[str, Any]:
    """One sub-agent = one agent_loop call in THIS thread (Flask already
    serves requests on threads; nested agent_loop is thread-safe because
    memory scope travels via a ContextVar).

    child_policy/spawn_depth are captured in the PARENT thread and re-set
    here: ContextVars and threading.local do NOT cross ThreadPoolExecutor
    submit boundaries, so the recursion guard and the guest-policy
    inheritance must be threaded through explicitly."""
    from agent_loop import _SUB_AGENT_POLICY  # imported lazily: import cycle
    policy_token = _SUB_AGENT_POLICY.set(child_policy)
    _SPAWN_DEPTH.value = spawn_depth
    started = datetime.now(timezone.utc)
    sub_ws = workspace_root / "agents" / f"agent-{index + 1}"
    sub_ws.mkdir(parents=True, exist_ok=True)
    try:
        from agent_loop import agent_loop
        reply = agent_loop(
            task,
            workspace_root=str(sub_ws),
            mode="local",
            max_iterations=MAX_SUB_AGENT_TURNS,
            print_final=False,
            policy=child_policy,
            request_id=request_id,
        )
    except Exception as exc:  # a crashed worker must not kill the fan-out
        return {"agent": index + 1, "workspace": f"agents/agent-{index + 1}",
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": int((datetime.now(timezone.utc) - started).total_seconds())}
    finally:
        _SPAWN_DEPTH.value = 0
        _SUB_AGENT_POLICY.reset(policy_token)
    return {"agent": index + 1, "workspace": f"agents/agent-{index + 1}",
            "ok": True, "reply": str(reply),
            "elapsed_s": int((datetime.now(timezone.utc) - started).total_seconds())}


def spawn_agents(tasks: list[str], workspace_root: str | Path, *,
                 request_id: str | None = None) -> dict[str, Any]:
    """Run up to MAX_SUB_AGENTS tasks in parallel, one sub-agent each.

    The RIGHT way (taught in the tool description + SYSTEM_PROMPT): spawn
    when the user explicitly asks for parallel work, or when the mission
    splits into 2-4 INDEPENDENT parts (different files, different topics).
    Steps that depend on each other must run in the MAIN agent instead —
    sub-agents cannot see each other's work.
    """
    if isinstance(tasks, str) or not isinstance(tasks, list):
        raise FileAgentError("tasks must be a list of task strings")
    tasks = [str(t).strip() for t in tasks if str(t).strip()]
    if not tasks:
        raise FileAgentError("tasks is empty — nothing to spawn")
    if len(tasks) > MAX_SUB_AGENTS:
        raise FileAgentError(
            f"{len(tasks)} tasks exceed the {MAX_SUB_AGENTS}-agent cap — "
            "split the mission across turns or pick the 4 that matter")
    if any(len(t) > MAX_TASK_CHARS for t in tasks):
        raise FileAgentError(f"each task must be at most {MAX_TASK_CHARS} chars")
    depth = getattr(_SPAWN_DEPTH, "value", 0)
    if depth > 0:
        # Sub-agents get the refusal as a RESULT (teaching pattern, same as
        # the zero-byte-write guard) so the loop can retry with a final.
        return {"ok": False, "refused": "recursion",
                "results": [{"agent": i + 1, "ok": False, "error": _SPAWN_ABSTRACT}
                            for i in range(len(tasks))],
                "note": "sub-agents cannot spawn sub-agents"}
    # Guest policy is inherited by every child (resolved in the PARENT
    # thread before any worker starts): a guest can never reach the owner's
    # machine through a sub-agent. "auto" resolves like the HTTP layer does.
    try:
        from agent_loop import _SUB_AGENT_POLICY
        inherited = _SUB_AGENT_POLICY.get(None)
    except Exception:  # agent_loop not importable in this context
        inherited = None
    child_policy = inherited or "auto"
    root = Path(workspace_root)
    try:
        workers = min(len(tasks), MAX_SUB_AGENTS)
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="aali-subagent") as pool:
            futures = [pool.submit(_run_sub_agent, task, i, root, request_id,
                                   child_policy, depth + 1)
                       for i, task in enumerate(tasks)]
            results: list[dict[str, Any]] = []
            for fut, task in zip(futures, tasks):
                try:
                    results.append(fut.result(timeout=SUB_AGENT_TIMEOUT_S))
                except TimeoutError:
                    results.append({"agent": len(results) + 1, "ok": False,
                                    "error": f"timed out after {SUB_AGENT_TIMEOUT_S}s",
                                    "task_preview": task[:80]})
                except Exception as exc:
                    results.append({"agent": len(results) + 1, "ok": False,
                                    "error": f"{type(exc).__name__}: {exc}"})
    finally:
        _SPAWN_DEPTH.value = 0
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": ok > 0,
            "spawned": len(tasks), "succeeded": ok,
            "results": results,
            "note": ("اجمع نتائج الوحدات في جواب واحد للمستخدم؛ "
                     "كل وحدة عملت في مجلدها agents/agent-N")}


_SUB_AGENT_TIMEOUT_S = SUB_AGENT_TIMEOUT_S  # re-export for tests/telemetry
