"""PC capability tour.

Exercises the REAL agent tools (file-agent.file_agent.file_tools) the same way
the agent would — writes stay inside the workspace (D:\\\\hwk-projects\\\\_pctour)
— and then does a READ-ONLY "reach" pass across many PC locations to show the
agent can inspect them. Everything is logged to a report file.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

WORKSPACE = Path("D:/hwk-projects")
REPORT = Path("D:/hwk-data/pc_tour_report.txt")
SANDBOX = "_pctour"  # agent-relative path inside the workspace

sys.path.insert(0, str(Path(".").resolve() / "file-agent"))
from file_agent.file_tools import execute_tool  # noqa: E402


def tool(name: str, **args) -> str:
    try:
        result = execute_tool(name, args, WORKSPACE)
        return f"OK   {name} -> {result}"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL {name} -> {type(exc).__name__}: {exc}"


def reach(path: str, depth: int = 1) -> str:
    """Read-only listing of a PC location (any depth)."""
    root = Path(path)
    lines: list[str] = []
    try:
        if not root.exists():
            return f"      {path}  (missing)"
        entries = sorted(root.iterdir())
        lines.append(f"      {path}  ({len(entries)} entries)")
        for entry in entries[:6]:
            kind = "DIR " if entry.is_dir() else "file"
            try:
                size = "" if entry.is_dir() else f" {entry.stat().st_size}b"
            except OSError:
                size = ""
            lines.append(f"        - {kind} {entry.name}{size}")
        if len(entries) > 6:
            lines.append(f"        ... and {len(entries) - 6} more")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"      {path}  ERROR {type(exc).__name__}: {exc}")
    return "\n".join(lines)


def main() -> None:
    out: list[str] = []
    out.append(f"=== HWK PC TOUR — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    out.append(f"Workspace (agent writes confined here): {WORKSPACE}\n")

    out.append("-- 1. Agent file tools (write/create/edit/move/delete, sandboxed) --")
    out.append(tool("make_directory", path=f"{SANDBOX}/notes"))
    out.append(tool("write_file", path=f"{SANDBOX}/notes/hello.txt",
                    content="مرحبا من HWK Aali\nThis is line two of the file.\n"))
    out.append(tool("append_file", path=f"{SANDBOX}/notes/hello.txt",
                    content="Appended line three.\n"))
    out.append(tool("replace_in_file", path=f"{SANDBOX}/notes/hello.txt",
                    old_text="line two", new_text="line TWO (edited)"))
    out.append(tool("read_file", path=f"{SANDBOX}/notes/hello.txt"))
    out.append(tool("search_files", pattern="مرحبا", path=SANDBOX))
    out.append(tool("list_files", path=SANDBOX, recursive=True))
    out.append(tool("move_file", source=f"{SANDBOX}/notes/hello.txt",
                    destination=f"{SANDBOX}/hello_moved.txt"))
    out.append(tool("write_file", path=f"{SANDBOX}/temp_delete_me.txt", content="bye"))
    out.append(tool("delete_file", path=f"{SANDBOX}/temp_delete_me.txt"))
    out.append(tool("run_command", command="python --version"))

    out.append("\n-- 2. PC reach (read-only inspection, no changes) --")
    for loc in ["C:/Users/HmamK", "C:/Users/HmamK/Desktop",
                "C:/Users/HmamK/Documents", "C:/Users/HmamK/Downloads",
                "D:/", "D:/hwk-data", "D:/hwk-projects", "X:/"]:
        out.append(reach(loc))

    report = "\n".join(out)
    REPORT.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
