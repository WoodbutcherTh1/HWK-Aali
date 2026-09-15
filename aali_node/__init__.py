"""Aali Node — the user-side execution half of Aali Cloud (STEP 5).

The Node runs on the USER's machine and is the ONLY thing that touches the
user's files there. The brain proposes tool calls; the Node executes them
inside this package's sandbox and returns signed results.

Modules
-------
sandbox      — SecureSandbox: workspace-jailed tool execution (security core)
__main__     — headless CLI daemon (`python -m aali_node`); bash/zsh/
               PowerShell/CMD all work through it, no UI required
confirm      — native/console consent dialogs (60s auto-deny)
updater      — signed auto-update client (verify → sha256 → staged unzip);
               the daemon runs it as a NON-FATAL pre-connect check

Deliberately NOT here yet (next sessions): the pywebview shell and the
system tray. The daemon is the load-bearing half: the shell is UI on top
of exactly these calls.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
