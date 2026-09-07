"""Small JSONL audit log for agent requests and tool execution."""

from __future__ import annotations

import json
import os
import queue
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LOG_FILE = Path(__file__).resolve().parent / "logs" / "agent.log"
MAX_LOGGED_STRING = 4_000

# Live-event bus: SSE clients subscribe by request_id and receive every
# log_event for that request as it happens (tool_requested, tool_result, ...).
# One dict, GIL-atomic operations, unbounded queues — the agent writes a
# handful of events per request, so there is no backpressure risk.
_subscribers: dict[str, "queue.Queue[dict[str, Any]]"] = {}


def subscribe(request_id: str) -> "queue.Queue[dict[str, Any]]":
    """Start receiving live events for a request (see app.py /api/ask/stream)."""
    q: "queue.Queue[dict[str, Any]]" = queue.Queue()
    _subscribers[request_id] = q
    return q


def unsubscribe(request_id: str) -> None:
    _subscribers.pop(request_id, None)


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
        pass
    # Live delivery to streaming clients — best effort, never breaks the agent.
    try:
        subscriber = _subscribers.get(request_id)
        if subscriber is not None:
            subscriber.put_nowait(entry)
    except Exception:  # noqa: BLE001
        pass