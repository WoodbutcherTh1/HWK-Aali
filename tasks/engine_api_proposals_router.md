# Engine API v1 — proposal-first edits, repo map, surface router

owner: Buffy
status: done
started: 2026-11-06
finished: 2026-11-06

## Scope

Implement the owner's four-part engine plan without UI work:

1. Remove the duplicate `file-agent/model_router/` package; keep the canonical
   `file_agent/model_router/`; add `docs/ENGINE_API.md`.
2. Add `file_agent/repo_map.py` with a gitignore-aware, token-budgeted
   repository map (tree-sitter optional, regex fallback).
3. Make `write_file` / `append_file` / `replace_in_file` proposal-first:
   stage under `.aali/staging/`, emit `proposal_created`, halt the turn, and
   apply only through `/api/approve`.
4. Add `switch_surface(surface_name, reason)` with the closed enum
   `chat|studio|graphics|control`, update the system prompt, and emit the
   `SURFACE_SWITCH` SSE event.

## Files

- `file-agent/file_agent/proposals.py` (new)
- `file-agent/file_agent/repo_map.py` (new)
- `docs/ENGINE_API.md` (new)
- `docs/model_router_int_report.md` (preserved from the removed duplicate dir)
- `file-agent/file_agent/file_tools.py`
- `file-agent/agent_loop.py`
- `file-agent/app.py`
- `file-agent/file_agent/model_router/__init__.py`
- `tests/test_proposals.py`, `tests/test_repo_map.py`,
  `tests/test_surface_router.py` (new)
- `tests/test_hwk.py`, `tests/test_tool_guard.py`,
  `tests/test_tool_orchestrator.py` (updated contracts/counts)

## Verification

- `python -m compileall` on the touched Python files: exit 0.
- `python -m pytest` over the new tests plus the affected agent/router/tool
  files: **229 passed, 1 skipped**.
- `git diff --check`: clean.

The full repository suite was not re-run in this step.

## Known limits

- The repo map is a callable engine primitive; it is not yet injected into
  every ask automatically.
- Proposals are workspace-scoped; remote Node approval is not wired.
- Proposal review UI is intentionally out of scope for this task.
