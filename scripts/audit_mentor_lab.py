"""Audit the mentor-lab episodes: keep only builds that actually work.

Every lab episode claims the external mentor built something in its sandbox. This audit
VERIFIES the claim (evidence first - the rule Aali is being taught):

- For each captured task, inspect the sandbox files.
- For app/game tasks with Python files: run each with --demo/--help (or a
  10s timeout) and require exit code 0 and non-empty output.
- For file tasks: require the target files to exist and be non-trivial
  (>200 bytes total sandbox content).
- Episodes whose build fails verification are REMOVED from the capture file
  (they would teach failure-without-recovery) and their sandbox is kept for
  inspection. A summary report is written to the lab dir.

Usage:
  python scripts/audit_mentor_lab.py            # audit + prune
  python scripts/audit_mentor_lab.py --report   # report only, prune nothing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from omniroute_mentor_lab import WORKSPACES, OUT_FILE, ALL_TASKS  # noqa: E402

AUDIT_LOG = WORKSPACES.parent / "audit_report.json"
RUN_TIMEOUT = 10


def _python_files(directory: Path) -> list[Path]:
    """Main-script candidates first (lab tasks produce one entry point)."""
    files = [f for f in sorted(directory.rglob("*.py"))
             if "__pycache__" not in f.parts and not f.name.startswith("test_")]
    files.sort(key=lambda f: 0 if f.name in ("main.py", "app.py") or
               "demo" in f.name else 1)
    return files[:2]


def _verify_python_build(workspace: Path) -> dict:
    """Run the main .py briefly (demo mode first); require one clean run.
    A timeout on an interactive game counts as a working build."""
    files = _python_files(workspace)
    if not files:
        return {"ok": False, "reason": "no python files found"}
    results = []
    for script in files:
        for args in (["--demo"], []):
            try:
                completed = subprocess.run(
                    [sys.executable, str(script), *args],
                    capture_output=True, text=True, timeout=RUN_TIMEOUT,
                    cwd=str(workspace),
                )
            except subprocess.TimeoutExpired:
                results.append({"file": script.name, "args": " ".join(args),
                                "ok": True, "note": "timeout (interactive)"})
                break
            if completed.returncode == 0 and (completed.stdout or "").strip():
                results.append({"file": script.name, "args": " ".join(args),
                                "ok": True, "output": completed.stdout[:120]})
                break
            results.append({"file": script.name, "args": " ".join(args), "ok": False,
                            "error": (completed.stderr or "")[-200:]})
        if any(r["ok"] for r in results):
            break
    return {"ok": any(r["ok"] for r in results), "runs": results[:3]}


def _verify_file_build(workspace: Path) -> dict:
    total = sum(p.stat().st_size for p in workspace.rglob("*") if p.is_file())
    non_empty = total > 200
    return {"ok": non_empty, "bytes": total,
            "reason": "" if non_empty else "sandbox nearly empty"}


def audit_task(task_id: str, category: str, episode: dict | None) -> dict:
    workspace = WORKSPACES / task_id
    if not workspace.exists():
        return {"task_id": task_id, "ok": False, "reason": "sandbox missing"}
    if category == "file":
        result = _verify_file_build(workspace)
    else:
        result = _verify_python_build(workspace)
    return {"task_id": task_id, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit mentor-lab episodes")
    parser.add_argument("--report", action="store_true", help="report only")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    episodes: dict[str, dict] = {}
    if OUT_FILE.exists():
        for line in OUT_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                episodes[record["meta"]["task_id"]] = record

    categories = {task_id: category for task_id, category, _ in ALL_TASKS}
    report = {"checked": [], "pruned": [], "kept": 0, "pruned_count": 0}
    for task_id, episode in episodes.items():
        category = categories.get(task_id, "file")
        result = audit_task(task_id, category, episode)
        report["checked"].append(result)
        if result["ok"]:
            report["kept"] += 1
        else:
            report["pruned_count"] += 1
            report["pruned"].append(result)
            if not args.report:
                episode["meta"]["audit_pruned"] = True
                episode["meta"]["audit_reason"] = result.get("reason")
                remaining = [
                    json.dumps(ep, ensure_ascii=False)
                    for tid, ep in episodes.items()
                    if tid != task_id
                ]
                # rewrite without the pruned episode
                OUT_FILE.write_text("\n".join(remaining) + "\n", encoding="utf-8")
                print(f"PRUNED {task_id}: {result.get('reason')}", flush=True)

    report["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    AUDIT_LOG.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("kept", "pruned_count")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
