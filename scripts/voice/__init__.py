# -*- coding: utf-8 -*-
"""Aali Voice Agent — real-time AR/HE/EN voice loop, all local, CPU-first."""
from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    Every voice entrypoint prints Arabic/Hebrew transcripts and replies. On
    Windows the default console encoding is cp1252, so printing Arabic raises
    UnicodeEncodeError and kills the run mid-log (this is what took down the
    first live chain). Same guard the rest of the repo uses.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass