"""Tool orchestrator — routes proposed tool calls to their execution target.

SaaS mode (AALI_MODE=saas): the brain never touches user machines directly.
The brain *proposes* tool calls; this module decides where each proposal is
allowed to run based on ``file_tools.TOOL_EXECUTION``:

- ``server`` tools run on the brain (web search, image/video/emoji, docs).
- ``client`` tools are dispatched to the user's Node (Aali Node) and executed
  inside the user's own sandboxed workspace.
- ``both`` tools (memory) run wherever context prefers.

Pure logic, no I/O: the same decision code runs on the brain, the Hub, and in
tests. Local mode never uses this module — every tool keeps executing directly
exactly as before.
"""

from __future__ import annotations

from typing import Any

from file_agent.file_tools import TOOL_EXECUTION, confirm_required

__all__ = [
    "ToolOrchestratorError",
    "RoutingDecision",
    "dispatch",
    "execution_target",
    "server_tools",
    "client_tools",
    "both_tools",
]


class ToolOrchestratorError(Exception):
    """Raised for orchestrator misuse (unknown preference, bad context)."""


def execution_target(tool: str) -> str | None:
    """Return the registered execution target for *tool*, or None."""
    return TOOL_EXECUTION.get(tool)


def server_tools() -> frozenset[str]:
    """Return the set of tools classified as server-side."""
    return frozenset(t for t, tgt in TOOL_EXECUTION.items() if tgt == "server")


def client_tools() -> frozenset[str]:
    """Return the set of tools classified as client-side."""
    return frozenset(t for t, tgt in TOOL_EXECUTION.items() if tgt == "client")


def both_tools() -> frozenset[str]:
    """Return the set of tools callable from either side."""
    return frozenset(t for t, tgt in TOOL_EXECUTION.items() if tgt == "both")


def dispatch(tool: str, *, prefer: str = "server", allow_client: bool = True,
             args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Route a proposed tool call and return a structured decision.

    Parameters
    ----------
    tool:
        The (already tool_guard-validated) tool name.
    prefer:
        For ``both``-target tools only: ``"server"`` (default) or
        ``"client"``. Misuse raises :class:`ToolOrchestratorError`.
    allow_client:
        When False, client tools are refused even in client-capable
        contexts (e.g. a role without client execution rights).
    args:
        Tool arguments — used only for conditional confirmation rules
        (``write_file`` + ``overwrite=true``).

    Returns
    -------
    dict with keys:
        ``tool``            — the input name (echoed for logging/audit)
        ``target``          — ``server`` | ``client`` | ``both``
        ``route``           — ``server`` | ``client`` | ``unroutable``
        ``requires_confirmation`` — bool (from
        :func:`file_agent.file_tools.confirm_required`)
        ``reason``          — human-readable explanation for the audit log
    """
    if prefer not in ("server", "client"):
        raise ToolOrchestratorError("prefer must be 'server' or 'client'")
    if not isinstance(tool, str) or not tool:
        raise ToolOrchestratorError("tool must be a non-empty string")

    target = TOOL_EXECUTION.get(tool)
    if target is None:
        # Defense in depth: tool_guard has already tried alias/fuzzy
        # correction upstream; anything still unknown is unroutable here.
        return {
            "tool": tool,
            "target": None,
            "route": "unroutable",
            "requires_confirmation": False,
            "reason": f"unknown tool: {tool!r} (not in TOOL_EXECUTION)",
        }

    if target == "server":
        return {
            "tool": tool,
            "target": "server",
            "route": "server",
            "requires_confirmation": confirm_required(tool, args),
            "reason": "server-resident tool (web/media/doc/skills)",
        }

    if target == "client":
        if not allow_client:
            return {
                "tool": tool,
                "target": "client",
                "route": "unroutable",
                "requires_confirmation": False,
                "reason": "client execution not allowed in this context",
            }
        return {
            "tool": tool,
            "target": "client",
            "route": "client",
            "requires_confirmation": confirm_required(tool, args),
            "reason": "workspace-touching tool — dispatch to user's Node",
        }

    # target == "both"
    route = prefer
    return {
        "tool": tool,
        "target": "both",
        "route": route,
        "requires_confirmation": confirm_required(tool, args),
        "reason": f"dual-target tool; context preference: {prefer}",
    }
