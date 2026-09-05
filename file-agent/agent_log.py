"""Small JSONL audit log for agent requests and tool execution."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LOG_FILE = Path(__file__).resolve().parent / "logs" / "agent.log"
MAX_LOGGED_STRING = 4_000


def log_file_path() -> Path:
    """Return the configured log path without exposing any secret values."""
    return Path(os.getenv("AGENT_LOG_FILE", DEFAULT_LOG_FILE)).expanduser()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_LOGGED_STRING:
            return value
        return f"{value[:MAX_LOGGED_STRING]}… [truncated]"
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def log_event(request_id: str, event: str, **data: Any) -> None:
    """Append one structured event; logging failures never break the agent."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "event": event,
        **_safe_value(data),
    }
    try:
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # The user's requested operation should still receive the real error
        # even if the filesystem is unable to create the audit log.
        return