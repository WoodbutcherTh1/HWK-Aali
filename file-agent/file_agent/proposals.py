"""Proposal-first edits — stage agent writes, apply only after approval.

The agent must never silently rewrite a user's file.  In proposal mode every
mutating text tool (``write_file``, ``append_file``, ``replace_in_file``)
writes a *candidate* into ``<workspace>/.aali/staging/`` and returns
``{"status": "pending_approval", ...}``.  Nothing in the real file changes.

The lifecycle is deliberately small and inspectable:

    pending  ──approve──▶  applied
       │
       └────reject────▶  rejected

An apply is CONFLICT-CHECKED: the file on disk must still equal the exact
content the proposal was built from.  If the user (or another agent) edited
it in the meantime, the apply is refused instead of clobbering the change.

Storage layout (inside the workspace, never outside it):

    .aali/staging/<id>.json     metadata (no inline file content)
    .aali/staging/<id>.before   exact pre-edit text (absent for new files)
    .aali/staging/<id>.after    exact proposed text

The staging directory is excluded from ``list_files``/``search_files`` and
from the repository map.  Direct human writes (the editor's Save button, the
Node sandbox, tests) call ``file_tools.write_file`` outside proposal mode and
keep their immediate-write contract.
"""
from __future__ import annotations

import contextlib
import contextvars
import difflib
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STAGING_DIR = ".aali/staging"
MAX_PROPOSAL_BYTES = 2_000_000
MAX_DIFF_CHARS = 20_000
MAX_LISTED = 100

_ID_RE = re.compile(r"^[0-9a-f]{16,64}$")
_PROPOSAL_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aali_proposal_mode", default=False
)


class ProposalError(Exception):
    """Expected, user-facing proposal error."""


class ProposalNotFound(ProposalError):
    """No proposal with this id exists in this workspace."""


class ProposalConflict(ProposalError):
    """The file changed after the proposal was created; refuse to clobber it."""


def proposal_mode_enabled() -> bool:
    """Global kill switch (``AALI_PROPOSAL_MODE=0``) for legacy tests/tools."""
    value = os.getenv("AALI_PROPOSAL_MODE", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


@contextlib.contextmanager
def proposal_mode(enabled: bool = True) -> Iterator[None]:
    """Scope proposal mode to THIS agent run (context-local, thread-safe)."""
    token = _PROPOSAL_MODE.set(bool(enabled) and proposal_mode_enabled())
    try:
        yield
    finally:
        _PROPOSAL_MODE.reset(token)


def is_proposal_mode() -> bool:
    return bool(_PROPOSAL_MODE.get())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str, encoding: str) -> str:
    return hashlib.sha256(text.encode(encoding)).hexdigest()


def _workspace_root(workspace_root: str | Path) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ProposalError(f"workspace is not a directory: {root}")
    return root


def _staging_root(workspace_root: str | Path, *, create: bool) -> tuple[Path, Path]:
    root = _workspace_root(workspace_root)
    staging = root / ".aali" / "staging"
    if create:
        staging.mkdir(parents=True, exist_ok=True)
    return root, staging


def _relative(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix() or "."


def _resolve(path: str, root: Path, *, must_exist: bool = False) -> Path:
    """Use the brain's ONE sandbox resolver (never a second, weaker copy)."""
    from file_agent.file_tools import FileAgentError
    from file_agent.file_tools import _resolve as resolve

    try:
        _root, target = resolve(path, root, must_exist=must_exist)
    except FileAgentError as exc:
        raise ProposalError(str(exc)) from exc
    return target


def _read_text(target: Path, encoding: str) -> str:
    try:
        return target.read_text(encoding=encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ProposalError(f"could not read {target.name}: {exc}") from exc
    except OSError as exc:
        raise ProposalError(f"could not read {target.name}: {exc}") from exc


def _diff(before: str | None, after: str, path: str) -> str:
    before_lines = (before if before is not None else "").splitlines()
    after_lines = after.splitlines()
    rendered = "\n".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"{path} (before)" if before is not None else "/dev/null",
            tofile=f"{path} (after)",
            lineterm="",
        )
    )
    if len(rendered) > MAX_DIFF_CHARS:
        rendered = rendered[:MAX_DIFF_CHARS] + "\n… diff truncated …"
    return rendered


def _save_meta(staging: Path, meta: dict[str, Any]) -> None:
    destination = staging / f"{meta['id']}.json"
    temporary = destination.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _load_meta(staging: Path, proposal_id: str) -> dict[str, Any]:
    proposal_id = str(proposal_id or "").strip()
    if not _ID_RE.fullmatch(proposal_id):
        raise ProposalNotFound("proposal id is invalid")
    path = staging / f"{proposal_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProposalNotFound(f"proposal not found: {proposal_id}") from exc
    except ValueError as exc:
        raise ProposalError(f"proposal metadata is unreadable: {proposal_id}") from exc
    if not isinstance(data, dict) or data.get("id") != proposal_id:
        raise ProposalNotFound(f"proposal not found: {proposal_id}")
    return data


def _write_payload(staging: Path, proposal_id: str, suffix: str,
                   text: str | None, encoding: str) -> None:
    path = staging / f"{proposal_id}.{suffix}"
    if text is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(text, encoding=encoding)


def _read_payload(staging: Path, proposal_id: str, suffix: str,
                  encoding: str) -> str | None:
    path = staging / f"{proposal_id}.{suffix}"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding=encoding)
    except (OSError, LookupError, UnicodeDecodeError) as exc:
        raise ProposalError(f"proposal payload is unreadable: {exc}") from exc


def _drop_payload(staging: Path, proposal_id: str) -> None:
    for suffix in ("before", "after"):
        (staging / f"{proposal_id}.{suffix}").unlink(missing_ok=True)


def _public(meta: dict[str, Any]) -> dict[str, Any]:
    """The client-facing shape — never the full file content."""
    return {
        "proposal_id": meta.get("id"),
        "path": meta.get("path"),
        "tool": meta.get("tool"),
        "status": meta.get("status"),
        "created_at": meta.get("created_at"),
        "applied_at": meta.get("applied_at"),
        "rejected_at": meta.get("rejected_at"),
        "before_exists": bool(meta.get("before_exists")),
        "bytes": meta.get("bytes"),
        "diff": meta.get("diff", ""),
        "reason": meta.get("reason", ""),
    }


def _stage(
    *,
    root: Path,
    target: Path,
    tool: str,
    before: str | None,
    after: str,
    encoding: str,
    request_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(after, str):
        raise ProposalError("content must be a string")
    try:
        encoded = after.encode(encoding)
    except LookupError as exc:
        raise ProposalError(f"unknown text encoding: {encoding}") from exc
    if len(encoded) > MAX_PROPOSAL_BYTES:
        raise ProposalError(
            f"proposed content is too large ({len(encoded)} bytes; "
            f"max {MAX_PROPOSAL_BYTES})")

    _root, staging = _staging_root(root, create=True)
    proposal_id = uuid.uuid4().hex[:16]
    rel = _relative(root, target)
    _write_payload(staging, proposal_id, "before", before, encoding)
    _write_payload(staging, proposal_id, "after", after, encoding)

    meta: dict[str, Any] = {
        "id": proposal_id,
        "tool": tool,
        "path": rel,
        "encoding": encoding,
        "before_exists": before is not None,
        "before_sha256": _sha(before, encoding) if before is not None else "",
        "after_sha256": _sha(after, encoding),
        "bytes": len(encoded),
        "created_at": _now_iso(),
        "status": "pending",
        "request_id": str(request_id or ""),
        "diff": _diff(before, after, rel),
        **(extra or {}),
    }
    _save_meta(staging, meta)
    return {
        "status": "pending_approval",
        "proposal_id": proposal_id,
        "path": rel,
        "tool": tool,
        "diff": meta["diff"],
        "bytes": meta["bytes"],
        "message": (
            f"الملف: {rel}. التعديل جاهز للمراجعة ولم يُكتب على القرص بعد. "
            "وافق عبر /api/approve أو ارفضه عبر /api/reject."
        ),
    }


# ---------------------------------------------------------------------------
# public staging API
# ---------------------------------------------------------------------------

def stage_write(
    path: str,
    content: str,
    workspace_root: str | Path,
    *,
    overwrite: bool = False,
    encoding: str = "utf-8",
    request_id: str = "",
) -> dict[str, Any]:
    if not isinstance(content, str):
        raise ProposalError("content must be a string")
    root = _workspace_root(workspace_root)
    target = _resolve(path, root)
    existed = target.exists()
    if existed and target.is_dir():
        raise ProposalError(f"path is a directory: {path}")
    if existed and not overwrite:
        raise ProposalError(
            f"file already exists: {_relative(root, target)}; pass overwrite=True")
    before = _read_text(target, encoding) if existed else None
    return _stage(root=root, target=target, tool="write_file", before=before,
                  after=content, encoding=encoding, request_id=request_id,
                  extra={"overwrite": bool(overwrite)})


def stage_append(
    path: str,
    content: str,
    workspace_root: str | Path,
    *,
    create: bool = False,
    encoding: str = "utf-8",
    request_id: str = "",
) -> dict[str, Any]:
    if not isinstance(content, str):
        raise ProposalError("content must be a string")
    root = _workspace_root(workspace_root)
    target = _resolve(path, root)
    existed = target.exists()
    if existed and target.is_dir():
        raise ProposalError(f"path is a directory: {path}")
    if not existed and not create:
        raise ProposalError(
            f"file does not exist: {_relative(root, target)}; pass create=True")
    before = _read_text(target, encoding) if existed else None
    after = (before or "") + content
    return _stage(root=root, target=target, tool="append_file", before=before,
                  after=after, encoding=encoding, request_id=request_id,
                  extra={"create": bool(create)})


def stage_replace(
    path: str,
    old_text: str,
    new_text: str,
    workspace_root: str | Path,
    *,
    replace_all: bool = False,
    encoding: str = "utf-8",
    request_id: str = "",
) -> dict[str, Any]:
    if not old_text:
        raise ProposalError("old_text must be non-empty")
    root = _workspace_root(workspace_root)
    target = _resolve(path, root, must_exist=True)
    if not target.is_file():
        raise ProposalError(f"path is not a file: {path}")
    before = _read_text(target, encoding)
    matches = before.count(old_text)
    if matches == 0:
        raise ProposalError("old_text was not found in the file")
    if matches > 1 and not replace_all:
        raise ProposalError(
            f"old_text matched {matches} times; pass replace_all=True")
    count = matches if replace_all else 1
    after = before.replace(old_text, new_text, count)
    return _stage(root=root, target=target, tool="replace_in_file",
                  before=before, after=after, encoding=encoding,
                  request_id=request_id,
                  extra={"replacements": count})


# ---------------------------------------------------------------------------
# approval API
# ---------------------------------------------------------------------------

def get_proposal(proposal_id: str, workspace_root: str | Path) -> dict[str, Any]:
    _root, staging = _staging_root(workspace_root, create=False)
    return _public(_load_meta(staging, proposal_id))


def list_proposals(workspace_root: str | Path, *, status: str | None = None,
                   limit: int = 20) -> dict[str, Any]:
    _root, staging = _staging_root(workspace_root, create=False)
    limit = max(1, min(int(limit), MAX_LISTED))
    rows: list[dict[str, Any]] = []
    if staging.is_dir():
        for path in sorted(staging.glob("*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or not meta.get("id"):
                continue
            if status and meta.get("status") != status:
                continue
            rows.append(meta)
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    pending = sum(1 for item in rows if item.get("status") == "pending")
    return {
        "proposals": [_public(item) for item in rows[:limit]],
        "pending": pending,
        "total": len(rows),
    }


def apply_proposal(proposal_id: str, workspace_root: str | Path) -> dict[str, Any]:
    """Apply a pending proposal, but only if the file still matches `before`."""
    root, staging = _staging_root(workspace_root, create=False)
    meta = _load_meta(staging, proposal_id)
    if meta.get("status") != "pending":
        raise ProposalError(
            f"proposal is already {meta.get('status')}: {proposal_id}")

    rel = str(meta.get("path") or "")
    target = _resolve(rel, root)
    encoding = str(meta.get("encoding") or "utf-8")
    before = _read_payload(staging, proposal_id, "before", encoding)
    after = _read_payload(staging, proposal_id, "after", encoding)
    if after is None:
        raise ProposalError(f"proposal payload is missing: {proposal_id}")
    expected_after = str(meta.get("after_sha256") or "")
    if expected_after and _sha(after, encoding) != expected_after:
        raise ProposalError(f"proposal payload integrity check failed: {proposal_id}")

    current = _read_text(target, encoding) if target.exists() else None
    if current != before:
        raise ProposalConflict(
            f"{rel} changed after this proposal was created; "
            "nothing was written. Re-run the edit against the current file.")

    from file_agent.file_tools import FileAgentError, _write_text

    try:
        _write_text(target, after, encoding)
    except FileAgentError as exc:
        raise ProposalError(f"could not apply proposal: {exc}") from exc
    meta.update({
        "status": "applied",
        "applied_at": _now_iso(),
        "diff": meta.get("diff", ""),
    })
    _save_meta(staging, meta)
    _drop_payload(staging, proposal_id)
    return _public(meta)


def reject_proposal(proposal_id: str, workspace_root: str | Path,
                    *, reason: str = "") -> dict[str, Any]:
    _root, staging = _staging_root(workspace_root, create=False)
    meta = _load_meta(staging, proposal_id)
    if meta.get("status") != "pending":
        raise ProposalError(
            f"proposal is already {meta.get('status')}: {proposal_id}")
    meta.update({
        "status": "rejected",
        "rejected_at": _now_iso(),
        "reason": str(reason or "")[:500],
    })
    _save_meta(staging, meta)
    _drop_payload(staging, proposal_id)
    return _public(meta)


def pending_payload(result: Any) -> dict[str, Any] | None:
    """Extract a staged-tool result from ``execute_tool``'s wrapper."""
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    value = result.get("result")
    if isinstance(value, dict) and value.get("status") == "pending_approval":
        return value
    return None


class ProposalPending(Exception):
    """Control-flow signal: the agent turn stopped at a pending proposal."""

    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = dict(proposal)
        super().__init__(str(proposal.get("proposal_id") or "proposal"))

    @property
    def user_message(self) -> str:
        item = self.proposal
        proposal_id = str(item.get("proposal_id") or "")
        path = str(item.get("path") or "")
        diff = str(item.get("diff") or "")
        preview = diff if len(diff) <= 1200 else diff[:1200] + "\n…"
        return (
            "تم إنشاء مقترح تعديل ولم يُكتب على القرص بعد.\n"
            f"المعرّف: {proposal_id}\n"
            f"الملف: {path}\n"
            "راجعه ثم وافق عبر POST /api/approve أو ارفضه عبر POST /api/reject.\n"
            "The edit is staged, not applied. Review it, then approve or reject it.\n"
            f"\n{preview}"
        )
