"""Sandboxed text-file operations exposed as function-callable tools."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class FileAgentError(Exception):
    """Expected, user-facing filesystem error."""


DEFAULT_MAX_CHARS = 100_000
DEFAULT_MAX_ENTRIES = 1_000


def _root(workspace_root: str | Path) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise FileAgentError(f"Workspace root is not a directory: {root}")
    return root


def _resolve(path: str, workspace_root: str | Path, *, must_exist: bool = False) -> tuple[Path, Path]:
    if not isinstance(path, str) or not path.strip():
        raise FileAgentError("path must be a non-empty relative string")
    root = _root(workspace_root)
    candidate = Path(path)
    if candidate.is_absolute():
        raise FileAgentError("Absolute paths are not allowed")
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise FileAgentError("Path must stay inside the workspace root") from exc
    if must_exist and not resolved.exists():
        raise FileAgentError(f"Path does not exist: {path}")
    return root, resolved


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _write_text(path: Path, content: str, encoding: str) -> None:
    try:
        content.encode(encoding)
    except LookupError as exc:
        raise FileAgentError(f"Unknown text encoding: {encoding}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError as exc:
        raise FileAgentError(f"Could not write file: {path.name}") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def list_files(path: str, workspace_root: str | Path, *, recursive: bool = False,
               max_entries: int = DEFAULT_MAX_ENTRIES) -> dict[str, Any]:
    if max_entries < 1:
        raise FileAgentError("max_entries must be at least 1")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_dir():
        raise FileAgentError(f"Path is not a directory: {path}")
    all_entries = sorted(
        target.rglob("*") if recursive else target.iterdir(),
        key=lambda item: item.as_posix().lower(),
    )
    entries = []
    for entry in all_entries[:max_entries]:
        item: dict[str, Any] = {
            "path": _relative(root, entry),
            "type": "symlink" if entry.is_symlink() else ("directory" if entry.is_dir() else "file"),
            "modified_at": datetime.fromtimestamp(
                entry.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
        if entry.is_file():
            item["size"] = entry.stat().st_size
        entries.append(item)
    return {
        "path": _relative(root, target),
        "entries": entries,
        "truncated": len(all_entries) > max_entries,
    }


def read_file(path: str, workspace_root: str | Path, *, encoding: str = "utf-8",
              max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    if max_chars < 1:
        raise FileAgentError("max_chars must be at least 1")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_file():
        raise FileAgentError(f"Path is not a file: {path}")
    try:
        content = target.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise FileAgentError(f"File is not valid {encoding} text") from exc
    except LookupError as exc:
        raise FileAgentError(f"Unknown text encoding: {encoding}") from exc
    return {
        "path": _relative(root, target),
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
        "size": len(content),
    }


def write_file(path: str, content: str, workspace_root: str | Path, *,
               overwrite: bool = False, encoding: str = "utf-8") -> dict[str, Any]:
    if not isinstance(content, str):
        raise FileAgentError("content must be a string")
    root, target = _resolve(path, workspace_root)
    existed = target.exists()
    if existed and target.is_dir():
        raise FileAgentError(f"Path is a directory: {path}")
    if existed and not overwrite:
        raise FileAgentError(f"File already exists: {_relative(root, target)}; pass overwrite=True")
    _write_text(target, content, encoding)
    return {
        "path": _relative(root, target),
        "bytes_written": len(content.encode(encoding)),
        "created": not existed,
        "overwritten": existed,
    }


def append_file(path: str, content: str, workspace_root: str | Path, *,
                create: bool = False, encoding: str = "utf-8") -> dict[str, Any]:
    if not isinstance(content, str):
        raise FileAgentError("content must be a string")
    root, target = _resolve(path, workspace_root)
    existed = target.exists()
    if existed and target.is_dir():
        raise FileAgentError(f"Path is a directory: {path}")
    if not existed and not create:
        raise FileAgentError(f"File does not exist: {_relative(root, target)}; pass create=True")
    existing = ""
    if existed:
        try:
            existing = target.read_text(encoding=encoding)
        except (LookupError, UnicodeDecodeError) as exc:
            raise FileAgentError(f"Could not read existing file: {path}") from exc
    _write_text(target, existing + content, encoding)
    return {
        "path": _relative(root, target),
        "bytes_appended": len(content.encode(encoding)),
        "created": not existed,
    }


def replace_in_file(path: str, old_text: str, new_text: str, workspace_root: str | Path, *,
                    replace_all: bool = False, encoding: str = "utf-8") -> dict[str, Any]:
    if not old_text:
        raise FileAgentError("old_text must be non-empty")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_file():
        raise FileAgentError(f"Path is not a file: {path}")
    try:
        original = target.read_text(encoding=encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise FileAgentError(f"Could not read existing file: {path}") from exc
    matches = original.count(old_text)
    if matches == 0:
        raise FileAgentError("old_text was not found in the file")
    if matches > 1 and not replace_all:
        raise FileAgentError(f"old_text matched {matches} times; pass replace_all=True")
    count = matches if replace_all else 1
    _write_text(target, original.replace(old_text, new_text, count), encoding)
    return {"path": _relative(root, target), "replacements": count}


def make_directory(path: str, workspace_root: str | Path, *, exist_ok: bool = False) -> dict[str, Any]:
    root, target = _resolve(path, workspace_root)
    if target.exists() and not target.is_dir():
        raise FileAgentError(f"Path exists and is not a directory: {path}")
    if target.exists() and not exist_ok:
        raise FileAgentError(f"Directory already exists: {path}")
    target.mkdir(parents=True, exist_ok=exist_ok)
    return {"path": _relative(root, target), "created": True}


def move_file(source: str, destination: str, workspace_root: str | Path, *,
              overwrite: bool = False) -> dict[str, Any]:
    root, source_path = _resolve(source, workspace_root, must_exist=True)
    _, destination_path = _resolve(destination, workspace_root)
    if destination_path.exists() and not overwrite:
        raise FileAgentError(f"Destination already exists: {_relative(root, destination_path)}")
    if source_path.is_dir() and destination_path.is_relative_to(source_path):
        raise FileAgentError("Cannot move a directory into itself")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if overwrite and destination_path.exists():
            shutil.rmtree(destination_path) if destination_path.is_dir() else destination_path.unlink()
        shutil.move(str(source_path), str(destination_path))
    except OSError as exc:
        raise FileAgentError(f"Could not move {source} to {destination}") from exc
    return {
        "source": _relative(root, source_path),
        "destination": _relative(root, destination_path),
        "overwritten": overwrite,
    }


def delete_file(path: str, workspace_root: str | Path, *, recursive: bool = False,
                missing_ok: bool = False) -> dict[str, Any]:
    root, target = _resolve(path, workspace_root)
    if not target.exists():
        if missing_ok:
            return {"path": _relative(root, target), "deleted": False}
        raise FileAgentError(f"Path does not exist: {path}")
    if target.is_dir() and not target.is_symlink():
        if not recursive:
            raise FileAgentError("Refusing to delete a directory without recursive=True")
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"path": _relative(root, target), "deleted": True}


ToolFunction = Callable[..., dict[str, Any]]
_FUNCTIONS: dict[str, ToolFunction] = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "append_file": append_file,
    "replace_in_file": replace_in_file,
    "make_directory": make_directory,
    "move_file": move_file,
    "delete_file": delete_file,
}


def _definition(name: str, description: str, properties: dict[str, Any],
                required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_DEFINITIONS = [
    _definition("list_files", "List files and directories inside the workspace.",
                {"path": {"type": "string"}, "recursive": {"type": "boolean", "default": False},
                 "max_entries": {"type": "integer", "minimum": 1, "default": 1000}}, ["path"]),
    _definition("read_file", "Read a UTF-8 text file inside the workspace.",
                {"path": {"type": "string"}, "encoding": {"type": "string", "default": "utf-8"},
                 "max_chars": {"type": "integer", "minimum": 1, "default": 100000}}, ["path"]),
    _definition("write_file", "Create or replace a text file; replacement requires overwrite=true.",
                {"path": {"type": "string"}, "content": {"type": "string"},
                 "overwrite": {"type": "boolean", "default": False},
                 "encoding": {"type": "string", "default": "utf-8"}}, ["path", "content"]),
    _definition("append_file", "Append text to a file; creation requires create=true.",
                {"path": {"type": "string"}, "content": {"type": "string"},
                 "create": {"type": "boolean", "default": False},
                 "encoding": {"type": "string", "default": "utf-8"}}, ["path", "content"]),
    _definition("replace_in_file", "Replace exact text; multiple matches require replace_all=true.",
                {"path": {"type": "string"}, "old_text": {"type": "string"},
                 "new_text": {"type": "string"}, "replace_all": {"type": "boolean", "default": False},
                 "encoding": {"type": "string", "default": "utf-8"}}, ["path", "old_text", "new_text"]),
    _definition("make_directory", "Create a directory and missing parent directories.",
                {"path": {"type": "string"}, "exist_ok": {"type": "boolean", "default": False}}, ["path"]),
    _definition("move_file", "Move a file or directory within the workspace.",
                {"source": {"type": "string"}, "destination": {"type": "string"},
                 "overwrite": {"type": "boolean", "default": False}}, ["source", "destination"]),
    _definition("delete_file", "Delete a file; directory deletion requires recursive=true.",
                {"path": {"type": "string"}, "recursive": {"type": "boolean", "default": False},
                 "missing_ok": {"type": "boolean", "default": False}}, ["path"]),
]


def get_tool_definitions() -> list[dict[str, Any]]:
    return json.loads(json.dumps(_DEFINITIONS))


def execute_tool(name: str, arguments: dict[str, Any], workspace_root: str | Path) -> dict[str, Any]:
    function = _FUNCTIONS.get(name)
    if function is None:
        return {"ok": False, "error": {"type": "unknown_tool", "message": f"Unknown tool: {name}"}}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": {"type": "invalid_arguments", "message": "arguments must be an object"}}
    try:
        return {"ok": True, "result": function(**arguments, workspace_root=workspace_root)}
    except TypeError as exc:
        return {"ok": False, "error": {"type": "invalid_arguments", "message": str(exc)}}
    except FileAgentError as exc:
        return {"ok": False, "error": {"type": "file_operation_error", "message": str(exc)}}
    except OSError as exc:
        return {"ok": False, "error": {"type": "file_operation_error", "message": str(exc)}}