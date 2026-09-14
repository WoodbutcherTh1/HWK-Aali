"""Secret redaction — stdlib-only, shared by the brain, the Hub and the Node.

Extracted from file_agent.memory (which hard-imports `requests`) so the
Aali Node can redact tool output on the user's machine without pulling the
training-venv dependency tree. Same patterns, same marker, one source of
truth: memory.py re-exports redact_secrets for backward compatibility.
"""
from __future__ import annotations

import re

__all__ = ["redact_secrets", "SECRET_MARKER"]

SECRET_MARKER = "[REDACTED-SECRET]"

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}\b"),                # openai-style keys
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}\b"),                  # bearer tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),                        # github tokens
    re.compile(r"(?i)\bAKIA[0-9A-Z]{12,}\b"),                             # aws access keys
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9\-]{8,}\b"),                  # slack tokens
    re.compile(r"(?i)(api[_\-]?key|api[_\-]?secret|password|passwd|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(مفتاح|كلمة\s+السر|الباسورد)\s*[:=]?\s*\S+"),          # Arabic secret mentions
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),                    # private keys
)


def redact_secrets(text: str) -> str:
    """Replace credential-looking substrings with a redaction marker."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(SECRET_MARKER, text)
    return text
