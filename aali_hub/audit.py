"""Aali Hub audit log — append-only JSONL of every tool_call.

Content-free by contract: each record holds who/what/when/where-outcome —
    {ts, user_id, tool, args_hash, status, duration_ms, node_id}
``args_hash`` is a SHA-256 over the canonical JSON of the tool arguments:
auditable and dedupable, but file contents and chat content NEVER enter the
log. The file is trimmed to the newest ``max_entries`` records on write.

Best-effort: a failed audit write must never break the action being audited.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["HubAudit", "args_hash"]

DEFAULT_MAX_ENTRIES = 2000


def args_hash(args: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON of tool arguments (never the raw args)."""
    canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class HubAudit:
    """Append-only, content-free tool_call audit trail."""

    def __init__(self, path: str | Path,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max(10, max_entries)

    def log_tool_call(self, user_id: str, tool: str, args: dict[str, Any],
                      status: str, *, node_id: str = "",
                      duration_ms: int = 0) -> None:
        """Append one tool_call event (never raises to the caller)."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "user_id": user_id,
            "tool": tool,
            "args_hash": args_hash(args),
            "status": status,
            "duration_ms": int(duration_ms),
            "node_id": node_id,
        }
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
            self._trim()
        except OSError:
            pass  # audit must never break the action it audits

    def _trim(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if len(lines) > self.max_entries:
                self.path.write_text(
                    "\n".join(lines[-self.max_entries:]) + "\n",
                    encoding="utf-8")
        except OSError:
            pass

    def query(self, *, limit: int = 100,
              tool: str | None = None) -> list[dict[str, Any]]:
        """Newest events first, optionally filtered by tool name."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in reversed(lines):
            if len(events) >= limit:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if tool is None or event.get("tool") == tool:
                events.append(event)
        return events
