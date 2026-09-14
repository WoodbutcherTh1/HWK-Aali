"""Aali Node sandbox — the security boundary for user-side execution.

Every proposed tool call from the brain arrives over the Hub and MUST be
validated here before anything runs. The rules (docs/SAAS_ARCHITECTURE.md §5):

1. Workspace jail — file arguments resolve under one root; escape attempts
   (absolute paths, ``..`` traversal, Windows drive prefixes, UNC shares,
   NTFS alternate data streams) are rejected, never cleaned up.
2. Own tool table — the Node re-derives what is executable client-side from
   its own table; anything unexpected is DENIED, not attempted.
3. Confirmation reconciliation — required when EITHER the proposal or the
   Node's own table says so (protocol.reconcile_confirmation). Denied tools
   are refused even if the user pressed "confirm" — confirmation never
   upgrades the tool set.
4. Timeouts — every execution is bounded by the proposal's timeout_sec
   (capped by MAX_TIMEOUT_SEC), so the brain cannot hang the user's machine.
5. Output hygiene — returned output passes the same secret-redaction used
   on the brain; nothing credential-looking leaves the machine.

Pure stdlib + the repo's own file_tools (imported lazily so the package can
be unit-tested in the hub venv, which has no training-venv deps).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):  # direct-script fallback: make repo imports work
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from file_agent import protocol as P  # noqa: E402

__all__ = [
    "MAX_TIMEOUT_SEC",
    "NODE_TOOLS",
    "SandboxDenied",
    "SandboxError",
    "SecureSandbox",
]

# Upper bound for any single tool execution, whatever the proposal claims.
MAX_TIMEOUT_SEC = 120

# Which arguments of which tools are FILESYSTEM PATHS (the only strings the
# workspace-jail check applies to). Everything else — file content, search
# patterns, commands — may legitimately contain colons, slashes and dots.
_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "list_files": ("path",),
    "read_file": ("path",),
    "read_image": ("path",),
    "write_file": ("path",),
    "append_file": ("path",),
    "replace_in_file": ("path",),
    "search_files": ("path",),
    "make_directory": ("path",),
    "delete_file": ("path",),
    "move_file": ("source", "destination"),
}


def _client_tools() -> frozenset[str]:
    """The Node's own client-tool table, read live from file_tools.

    Read per call (never cached): a brain-side registry update must be
    visible here without restarting the Node — the same policy the
    tool_guard follows. Import inside the function so importing THIS module
    never pulls the training-venv dependency tree in constrained venvs.
    """
    from file_agent.file_tools import TOOL_EXECUTION

    return frozenset(t for t, tgt in TOOL_EXECUTION.items() if tgt == "client")


# Alias kept for readability at call sites / tests.
NODE_TOOLS = _client_tools


class SandboxError(Exception):
    """Execution failed inside the sandbox (tool error, timeout, crash)."""


class SandboxDenied(Exception):
    """The proposal was REFUSED before execution (jail/table/confirmation)."""


class SecureSandbox:
    """Jailed executor for brain-proposed tool calls on the user's machine.

    Parameters
    ----------
    workspace_root:
        The ONLY directory this sandbox may touch. Created on demand.
    allow_commands:
        Passed through to file_tools.run_command (HWK_ALLOW_COMMANDS is the
        brain-side switch; the Node keeps its own explicit flag so a user
        can run the Node with commands disabled regardless of env).
    confirm_hook:
        Optional callable(requires_confirmation: bool, tool: str, args: dict)
        -> bool. When the call needs confirmation, this asks the human (the
        pywebview shell will show a native dialog here). Returning False
        denies the call. None + required confirmation => auto-deny
        (headless-safe: no dialog can appear, so nothing dangerous runs
        unattended). 60s silence = auto-deny remains the shell's job.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        allow_commands: bool = True,
        confirm_hook: Callable[[bool, str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.root = Path(workspace_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allow_commands = bool(allow_commands)
        self.confirm_hook = confirm_hook

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def _deny_if_unknown(self, tool: str) -> None:
        if tool not in _client_tools():
            raise SandboxDenied(
                f"tool {tool!r} is not executable on this Node"
            )

    def _deny_if_escaped(self, tool: str, args: dict[str, Any]) -> None:
        """Validate only the path-typed arguments of *tool* against the jail."""
        from file_agent.file_tools import _resolve

        names = _PATH_ARGS.get(tool)
        if not names:
            return  # run_command / machine_ops take no filesystem paths
        for name in names:
            value = args.get(name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise SandboxDenied(
                    f"argument {name!r} must be a string path")
            candidate = value.strip()
            if not candidate or candidate not in (".", ".."):
                # cheap structural pre-checks with distinct, honest errors
                # (UNC first: "//server" also matches the "/" rule below)
                lowered = candidate.lower()
                if "\x00" in candidate:
                    raise SandboxDenied("NUL bytes are not allowed in paths")
                if candidate.startswith("//") or candidate.startswith("\\\\"):
                    raise SandboxDenied(
                        "UNC paths are not allowed inside the workspace")
                if ":" in candidate:
                    if len(candidate) >= 2 and candidate[1] == ":":
                        # drive prefix ("D:/x", drive-relative "C:foo")
                        raise SandboxDenied(
                            "absolute paths are not allowed inside the "
                            "workspace")
                    # a colon anywhere else in a relative path is an NTFS
                    # alternate data stream attempt ("notes.txt:secret")
                    raise SandboxDenied(
                        "NTFS alternate data streams are not allowed "
                        "inside the workspace")
                if lowered.startswith("/"):  # POSIX-absolute
                    raise SandboxDenied(
                        "absolute paths are not allowed inside the workspace")
            # the authoritative check: _resolve must accept it (this is the
            # SAME jail the brain's own tools use — one implementation)
            try:
                _resolve(candidate, self.root)
            except Exception as exc:
                raise SandboxDenied(str(exc)) from exc

    def _confirm(self, tool: str, args: dict[str, Any],
                 proposal_flag: bool) -> bool:
        """Reconcile confirmation (either side may demand it) and ask."""
        from file_agent.file_tools import confirm_required

        required = P.reconcile_confirmation(
            proposal_flag, confirm_required(tool, args))
        if not required:
            return True
        if self.confirm_hook is None:
            return False  # headless + confirmation needed => auto-deny
        return bool(self.confirm_hook(True, tool, args))

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def execute(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Validate + execute one signed tool_call; return a tool_result dict.

        Never raises for refusals: denials come back as status="denied",
        execution failures as status="error" — the brain can read both and
        retry or explain. Only truly broken proposals (not dicts) raise.
        """
        if not isinstance(proposal, dict):
            raise SandboxError("tool_call proposal must be a dict")

        call_id = str(proposal.get("id") or "")
        tool = proposal.get("tool")
        args = proposal.get("args")
        args = args if isinstance(args, dict) else {}

        started = time.monotonic()

        def result(status: str, payload: dict[str, Any]) -> dict[str, Any]:
            return P.make_tool_result(
                call_id, status, payload,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if not call_id:
            raise SandboxError("tool_call is missing its id")
        if not isinstance(tool, str) or not tool:
            return result("denied", {"error": "missing tool name"})

        try:
            self._deny_if_unknown(tool)
        except SandboxDenied as exc:
            return result("denied", {"error": str(exc)})

        try:
            self._deny_if_escaped(tool, args)
        except SandboxDenied as exc:
            return result("denied", {"error": f"path rejected: {exc}"})

        if tool == "run_command":
            if not self.allow_commands:
                return result("denied",
                              {"error": "commands disabled on this Node"})
            raw = str(args.get("command") or "")
            from file_agent.file_tools import _looks_like_secret_exfiltration
            if _looks_like_secret_exfiltration(raw):
                return result("denied", {
                    "error": "command would expose environment variables "
                             "or credentials; refused on this machine",
                })

        if not self._confirm(tool, args,
                             bool(proposal.get("requires_confirmation"))):
            return result("denied", {
                "error": "user declined (or was unavailable to confirm) "
                         f"this {tool} call",
            })

        timeout = self._timeout_of(proposal)
        try:
            payload = self._dispatch(tool, args, timeout)
            return result("ok", self._redacted(payload))
        except SandboxError as exc:
            message = str(exc)
            status = "timeout" if "timed out" in message else "error"
            return result(status, {"error": message})
        except Exception as exc:  # tool crash: report, never propagate
            return result("error", {"error": f"{type(exc).__name__}: {exc}"})

    def _timeout_of(self, proposal: dict[str, Any]) -> int:
        try:
            requested = int(proposal.get("timeout_sec") or 60)
        except (TypeError, ValueError):
            requested = 60
        return max(1, min(requested, MAX_TIMEOUT_SEC))

    def _dispatch(self, tool: str, args: dict[str, Any],
                  timeout: int) -> dict[str, Any]:
        """Call the real file_tools implementation by name (no eval)."""
        import file_agent.file_tools as ft

        root = self.root
        if tool == "list_files":
            return ft.list_files(args.get("path", "."), root,
                                 recursive=bool(args.get("recursive", False)))
        if tool == "read_file":
            return ft.read_file(args.get("path", ""), root,
                                encoding=str(args.get("encoding") or "utf-8"))
        if tool == "read_image":
            return ft.read_image(args.get("path", ""), root)
        if tool == "write_file":
            return ft.write_file(args.get("path", ""),
                                 str(args.get("content") or ""), root,
                                 overwrite=bool(args.get("overwrite", False)))
        if tool == "append_file":
            return ft.append_file(args.get("path", ""),
                                  str(args.get("content") or ""), root)
        if tool == "replace_in_file":
            return ft.replace_in_file(args.get("path", ""),
                                      str(args.get("old_text") or ""),
                                      str(args.get("new_text") or ""), root)
        if tool == "search_files":
            return ft.search_files(args.get("pattern", ""), root,
                                   path=str(args.get("path") or "."))
        if tool == "make_directory":
            return ft.make_directory(args.get("path", ""), root,
                                     exist_ok=bool(args.get("exist_ok", False)))
        if tool == "move_file":
            return ft.move_file(args.get("source", ""),
                                args.get("destination", ""), root,
                                overwrite=bool(args.get("overwrite", False)))
        if tool == "delete_file":
            return ft.delete_file(args.get("path", ""), root,
                                  recursive=bool(args.get("recursive", False)))
        if tool == "run_command":
            return ft.run_command(str(args.get("command") or ""), root,
                                  timeout_seconds=timeout)
        if tool == "machine_ops":
            return ft.machine_ops(str(args.get("action") or ""), root,
                                  target=str(args.get("target") or ""))
        raise SandboxError(f"tool {tool!r} has no node-side binding")

    def _redacted(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Redact credential-looking strings before anything leaves the box."""
        # stdlib-only module on purpose: file_agent.memory pulls `requests`,
        # which the node venv deliberately does not carry.
        from file_agent.redaction import redact_secrets

        out: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                out[key] = redact_secrets(value)
            elif isinstance(value, list):
                out[key] = [
                    redact_secrets(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                out[key] = value
        return out
