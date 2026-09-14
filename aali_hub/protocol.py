"""Aali Hub protocol shim — re-exports the shared signed-message protocol.

The single source of truth for the wire protocol is
``file-agent/file_agent/protocol.py`` (stdlib-only, tested there). The Hub
re-exports it so hub modules can ``from aali_hub import protocol`` without
duplicating code or drifting. Deploying the Hub ships the repo, so the shared
module is always present.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from file_agent import protocol as _protocol
except ImportError:  # Hub venv running from a checkout — bootstrap the path
    _shared = Path(__file__).resolve().parents[1] / "file-agent"
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from file_agent import protocol as _protocol

__all__ = list(getattr(_protocol, "__all__", []))

globals().update({name: getattr(_protocol, name)
                  for name in _protocol.__all__})
