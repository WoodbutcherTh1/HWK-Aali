"""Content-free logging for Jarvis.

The brief is explicit: never log secrets, and keep logs content-free. So a
record may carry an event name, a machine key, sizes and booleans — but never
message text, tokens, API keys, prompts, replies or audio.

Every field is checked against a whitelist before it is written, so adding a
``text=`` kwarg by mistake cannot leak a conversation into the log.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

# The ONLY keys that may ever be written. Anything else is dropped.
ALLOWED_FIELDS = frozenset(
    {
        "event",
        "machine",
        "ok",
        "status",
        "http",
        "bytes",
        "chars",
        "secs",
        "count",
        "model",
        "reason",
        "cmd",
        "sender",
    }
)

# Keys that are secrets-ish; dropped even though they look harmless.
FORBIDDEN_FIELDS = frozenset(
    {
        "text",
        "message",
        "reply",
        "prompt",
        "token",
        "key",
        "bot_token",
        "api_key",
        "authorization",
        "chat_id",
        "content",
        "transcript",
        "audio",
    }
)


def log_path(data_root: str | Path | None = None) -> Path:
    if data_root:
        return Path(data_root) / "jarvis.log"
    return Path.home() / ".aali" / "jarvis" / "jarvis.log"


def _sha16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def sanitize(fields: dict[str, Any]) -> dict[str, Any]:
    """Keep only whitelisted, non-secret fields; hash anything text-like."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in FORBIDDEN_FIELDS or key not in ALLOWED_FIELDS:
            continue
        if isinstance(value, str) and len(value) > 64:
            out[key] = _sha16(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = _sha16(str(value))
    return out


def log_event(event: str, path: str | Path | None = None, **fields: Any) -> None:
    """Append one content-free JSON line. Never raises."""
    try:
        record: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        record["event"] = event
        record.update(sanitize(fields))
        target = Path(path) if path else log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must never break the bot.
        pass


def text_fingerprint(text: str) -> str:
    """A short, non-reversible tag for a piece of text (used for correlation)."""
    return _sha16(text)