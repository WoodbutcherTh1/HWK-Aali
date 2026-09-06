"""Sandboxed text-file operations exposed as function-callable tools."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class FileAgentError(Exception):
    """Expected, user-facing filesystem error."""


DEFAULT_MAX_CHARS = 100_000
DEFAULT_MAX_ENTRIES = 1_000

# Directories never searched or listed recursively (build junk / VCS / deps).
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".idea", ".vscode", "target", "bin", "obj",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
# File extensions treated as binary (never opened by search_files).
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".gz", ".zst", ".xz", ".bin", ".pyc", ".woff", ".woff2", ".ttf",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".jar", ".class",
}
# Commands the agent may run itself. Everything executes with the user's own
# account permissions with cwd inside the workspace, and is written to the
# agent log; treat this as a convenience guard, not a security boundary.
ALLOWED_COMMANDS = {
    "python", "python3", "py", "pip", "pip3", "pytest",
    "node", "npm", "npx", "yarn", "pnpm",
    "git", "dir", "ls", "echo", "pwd", "where", "which",
    "gcc", "g++", "clang", "cargo", "go", "make", "cmake", "dotnet",
}


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


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


def read_image(path: str, workspace_root: str | Path, *, max_lines: int = 300) -> dict[str, Any]:
    """Extract text from an image file with the Windows built-in OCR engine.

    This is the text-only bridge for image input: the model cannot "see"
    pixels yet, but it can read the text an image contains (signs, screenshots,
    documents, photos of pages). True vision understanding is on the roadmap.
    """
    if max_lines < 1:
        raise FileAgentError("max_lines must be at least 1")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_file():
        raise FileAgentError(f"Path is not a file: {path}")
    if target.suffix.lower() not in IMAGE_SUFFIXES:
        raise FileAgentError(f"Not a supported image file: {path} (expected one of {sorted(IMAGE_SUFFIXES)})")
    script = Path(__file__).resolve().parents[2] / "win_ocr.ps1"
    if not script.is_file():
        raise FileAgentError("OCR helper (win_ocr.ps1) not found next to the project")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise FileAgentError("OCR timed out") from exc
    if proc.returncode != 0:
        raise FileAgentError("OCR failed: " + (proc.stderr or proc.stdout or "")[-500:])
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise FileAgentError("OCR returned unreadable output") from exc
    if isinstance(items, dict):  # ConvertTo-Json unwraps single-element arrays
        items = [items]
    text_lines = [str(item.get("text", "")).strip() for item in items if item.get("text")]
    if not text_lines:
        return {
            "path": _relative(root, target),
            "text": "",
            "note": "No readable text found in this image (blank, graphics-only, or OCR language unavailable).",
        }
    return {
        "path": _relative(root, target),
        "text": "\n".join(text_lines[:max_lines]),
        "lines": len(text_lines),
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


def search_files(pattern: str, workspace_root: str | Path, *, path: str = ".",
                 ignore_case: bool = True, max_matches: int = 200) -> dict[str, Any]:
    """Search text files inside the workspace for a regex, with line numbers."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise FileAgentError("pattern must be a non-empty string")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_dir():
        raise FileAgentError(f"Path is not a directory: {path}")
    try:
        expression = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise FileAgentError(f"Invalid regular expression: {exc}") from exc
    matches: list[dict[str, Any]] = []
    scanned_files = 0
    for entry in target.rglob("*"):
        if not entry.is_file():
            continue
        if entry.suffix.lower() in BINARY_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in entry.parts):
            continue
        if entry.stat().st_size > 2_000_000:
            continue  # avoid scanning huge files
        try:
            text = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned_files += 1
        for line_number, line in enumerate(text.splitlines(), 1):
            if expression.search(line):
                matches.append({
                    "path": _relative(root, entry),
                    "line": line_number,
                    "text": line.strip()[:250],
                })
                if len(matches) >= max_matches:
                    return {
                        "pattern": pattern,
                        "matches": matches,
                        "truncated": True,
                        "scanned_files": scanned_files,
                    }
    return {
        "pattern": pattern,
        "matches": matches,
        "truncated": False,
        "scanned_files": scanned_files,
    }


def run_command(command: str, workspace_root: str | Path, *,
                timeout_seconds: int = 120, max_output_chars: int = 20000) -> dict[str, Any]:
    """Run an allow-listed build/test/run command inside the workspace.

    Disabled entirely unless the HWK_ALLOW_COMMANDS environment variable is
    set to a value other than "0" (the app defaults it to "1"). Commands run
    with the user's own permissions and are logged for review.
    """
    if os.getenv("HWK_ALLOW_COMMANDS", "1") == "0":
        raise FileAgentError("run_command is disabled (HWK_ALLOW_COMMANDS=0)")
    if not isinstance(command, str) or not command.strip():
        raise FileAgentError("command must be a non-empty string")
    parts = shlex.split(command, posix=True)
    base = parts[0].lower()
    if base not in ALLOWED_COMMANDS:
        raise FileAgentError(f"Command '{base}' is not in the allow-list.")
    if base == "git" and len(parts) > 1 and parts[1].lower() in {
        "push", "reset", "clean", "rebase", "cherry-pick", "merge",
    }:
        raise FileAgentError("Destructive or remote git commands are not allowed.")
    root = _root(workspace_root)
    started = time.time()
    try:
        completed = subprocess.run(
            parts,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired:
        raise FileAgentError(f"Command timed out after {timeout_seconds}s.") from None
    except OSError as exc:
        raise FileAgentError(f"Could not start command: {exc}") from None
    duration_ms = int((time.time() - started) * 1000)
    if completed.returncode != 0:
        raise FileAgentError(
            f"Command exited with code {completed.returncode}. Output:\n{output[:4000]}"
        )
    return {
        "command": command,
        "exit_code": 0,
        "duration_ms": duration_ms,
        "output": output[:max_output_chars],
        "output_truncated": len(output) > max_output_chars,
        "cwd": _relative(root, root),
    }


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


VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".webm"}


def analyze_video(path: str, workspace_root: str | Path, *, frame_interval: float = 5.0,
                  max_frames: int = 12, transcript: bool = True) -> dict[str, Any]:
    """Understand a video: OCR of sampled frames + Whisper transcript of audio.

    Runs scripts/video_tools.py in a subprocess so heavy imports (cv2, whisper)
    never bloat the agent process.
    """
    if frame_interval <= 0 or max_frames < 1:
        raise FileAgentError("frame_interval must be > 0 and max_frames >= 1")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_file():
        raise FileAgentError(f"Path is not a file: {path}")
    if target.suffix.lower() not in VIDEO_SUFFIXES:
        raise FileAgentError(f"Unsupported video type: {target.suffix} (expected one of {sorted(VIDEO_SUFFIXES)})")
    script = Path(__file__).resolve().parents[2] / "scripts" / "video_tools.py"
    if not script.is_file():
        raise FileAgentError("video_tools.py not found")
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(script), str(target),
             "--interval", str(frame_interval), "--max-frames", str(max_frames),
             "--no-transcript" if not transcript else "--transcript"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        raise FileAgentError("Video analysis timed out") from exc
    if proc.returncode != 0:
        raise FileAgentError("Video analysis failed: " + (proc.stderr or proc.stdout or "")[-500:])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FileAgentError("Unreadable video analysis output") from exc

def generate_image(prompt: str, path: str, workspace_root: str | Path, *,
                   negative_prompt: str = "", steps: int = 25, guidance_scale: float = 7.5,
                   width: int = 512, height: int = 512, seed: int | None = None) -> dict[str, Any]:
    """Generate an image from a text prompt with a local Stable Diffusion model.

    Runs scripts/image_gen_tool.py in a subprocess (torch + diffusers stay out
    of the main agent process, same reasoning as analyze_video). The model
    weights (~2GB) download once on first use and are cached afterward.
    Note: this shares the GPU with any active Aali training run - expect it
    to be slower, or to fall back to CPU, while training is going.
    """
    if not prompt or not prompt.strip():
        raise FileAgentError("prompt must be non-empty")
    if steps < 1:
        raise FileAgentError("steps must be at least 1")
    if width < 64 or height < 64:
        raise FileAgentError("width/height must be at least 64")
    root, target = _resolve(path, workspace_root)
    if target.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise FileAgentError("path must end in .png, .jpg, or .jpeg")
    if target.exists():
        raise FileAgentError(f"File already exists: {_relative(root, target)}; choose a different path")
    script = Path(__file__).resolve().parents[2] / "scripts" / "image_gen_tool.py"
    if not script.is_file():
        raise FileAgentError("image_gen_tool.py not found")
    command = [
        sys.executable, "-u", str(script), prompt,
        "--output", str(target), "--negative-prompt", negative_prompt,
        "--steps", str(steps), "--guidance", str(guidance_scale),
        "--width", str(width), "--height", str(height),
    ]
    if seed is not None:
        command += ["--seed", str(seed)]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,  # first run downloads the model; generation itself is much faster
        )
    except subprocess.TimeoutExpired as exc:
        raise FileAgentError("Image generation timed out (first run downloads the model - try again once it's cached)") from exc
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        result = {}
    if proc.returncode != 0 or "error" in result:
        message = result.get("message") if result else None
        raise FileAgentError(message or "Image generation failed: " + (proc.stderr or proc.stdout or "")[-500:])
    result["path"] = _relative(root, target)
    return result


DOC_SUFFIXES = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx"}


def read_document(path: str, workspace_root: str | Path, *, max_chars: int = 60_000) -> dict[str, Any]:
    """Read a PDF/DOCX/XLSX file from the workspace and return its text."""
    if max_chars < 500:
        raise FileAgentError("max_chars must be at least 500")
    root, target = _resolve(path, workspace_root, must_exist=True)
    if not target.is_file():
        raise FileAgentError(f"Path is not a file: {path}")
    suffix = target.suffix.lower()
    if suffix not in DOC_SUFFIXES:
        raise FileAgentError(f"Unsupported document type: {suffix} (expected one of {sorted(DOC_SUFFIXES)})")

    from file_agent import doc_tools

    kind = DOC_SUFFIXES[suffix]
    if kind == "pdf":
        result = doc_tools.read_pdf(target, max_chars=max_chars)
    elif kind == "docx":
        result = doc_tools.read_docx(target, max_chars=max_chars)
    else:
        result = doc_tools.read_xlsx(target, max_chars=max_chars)
    result["path"] = _relative(root, target)
    result["kind"] = kind
    return result


def make_n8n_workflow(description: str, workspace_root: str | Path, *,
                      filename: str = "") -> dict[str, Any]:
    """Generate an importable n8n workflow from a natural-language description."""
    if not isinstance(description, str) or not description.strip():
        raise FileAgentError("description must be a non-empty string")
    from file_agent import n8n_gen

    workflow = n8n_gen.generate_workflow(description)
    root, base = _resolve("n8n_workflows", workspace_root)
    base.mkdir(parents=True, exist_ok=True)
    name = filename.strip() or n8n_gen.safe_filename(description)
    if not name.endswith(".json"):
        name += ".json"
    target = base / name
    target.write_text(json.dumps(workflow, ensure_ascii=False, indent=1), encoding="utf-8")
    node_names = [node.get("name", "") for node in workflow.get("nodes", [])]
    return {
        "path": _relative(root, target),
        "workflow_name": workflow["name"],
        "nodes": node_names,
        "import_steps": (
            "افتح n8n (http://localhost:5678) → Workflows → Import from File → "
            "اختر الملف من D:\\hwk-projects\\n8n_workflows ثم فعّل ToggleActive"
        ),
        "webhook_note": (
            "لو بدأ السير بعقدة Webhook فسيكون الرابط: "
            "http://localhost:5678/webhook/<path-from-node> (يظهر في عقدة الويبهوك بعد الاستيراد)"
        ),
    }


def list_skills(workspace_root: str | Path) -> dict[str, Any]:
    """List the available skills (name + one-line description)."""
    from file_agent import skills as _skills
    return _skills.list_skills(workspace_root)


def use_skill(name: str, workspace_root: str | Path) -> dict[str, Any]:
    """Load a skill's full instructions by name (see list_skills)."""
    from file_agent import skills as _skills
    try:
        return _skills.use_skill(name, workspace_root)
    except FileNotFoundError as exc:
        raise FileAgentError(str(exc)) from exc


def fetch_url(url: str, workspace_root: str | Path, *,
               max_chars: int = 20000) -> dict[str, Any]:
    """Fetch a web page and return its readable text."""
    from file_agent import web_tools as _web_tools
    return _web_tools.fetch_url(url, workspace_root, max_chars=max_chars)


def web_search(query: str, workspace_root: str | Path, *,
               max_results: int = 6) -> dict[str, Any]:
    """Keyless web search (DuckDuckGo HTML endpoint); no API key needed."""
    from file_agent import web_tools as _web_tools
    return _web_tools.web_search(query, workspace_root, max_results=max_results)


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
    "search_files": search_files,
    "run_command": run_command,
    "read_image": read_image,
    "read_document": read_document,
    "analyze_video": analyze_video,
    "make_n8n_workflow": make_n8n_workflow,
    "list_skills": list_skills,
    "use_skill": use_skill,
    "fetch_url": fetch_url,
    "web_search": web_search,
    "generate_image": generate_image,
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
    _definition("search_files", "Search text files in the workspace for a regex pattern; returns file/line matches.",
                {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."},
                 "ignore_case": {"type": "boolean", "default": True},
                 "max_matches": {"type": "integer", "default": 200}}, ["pattern"]),
    _definition("run_command", "Run an allow-listed build/test/run command (python, pip, pytest, node, npm, git, compilers) with cwd inside the workspace; never use for destructive or system-wide operations.",
                {"command": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 120},
                 "max_output_chars": {"type": "integer", "default": 20000}}, ["command"]),
    _definition("read_image", "Extract (OCR) the text inside an image file: screenshots, documents, signs, photos of pages. The model reads the text, not the pixels.",
                {"path": {"type": "string"}, "max_lines": {"type": "integer", "default": 300}}, ["path"]),
    _definition("generate_image", "Generate a new image from a text description using a local Stable Diffusion model, and save it to the workspace. Shares the GPU with any running Aali training - may be slow or fall back to CPU while training is active.",
                {"prompt": {"type": "string"}, "path": {"type": "string"},
                 "negative_prompt": {"type": "string", "default": ""},
                 "steps": {"type": "integer", "minimum": 1, "default": 25},
                 "width": {"type": "integer", "default": 512}, "height": {"type": "integer", "default": 512},
                 "seed": {"type": "integer"}}, ["prompt", "path"]),
    _definition("read_document", "Read a document file (.pdf, .docx, .xlsx) and return its text content.",
                {"path": {"type": "string"}, "max_chars": {"type": "integer", "default": 60000}}, ["path"]),
    _definition("analyze_video", "Understand a video file (.mp4/.avi/.mkv/.mov): read on-screen text from sampled frames (OCR) and transcribe the audio (Whisper).",
                {"path": {"type": "string"}, "frame_interval": {"type": "number", "default": 5.0},
                 "max_frames": {"type": "integer", "default": 12}, "transcript": {"type": "boolean", "default": True}}, ["path"]),
    _definition("make_n8n_workflow", "Generate an importable n8n workflow JSON from an Arabic/English description (triggers: webhook/schedule/email; actions: Aali brain, HTTP, Telegram). Returns the file path plus import steps.",
                {"description": {"type": "string"}, "filename": {"type": "string", "default": ""}}, ["description"]),
    _definition("list_skills", "List available skills (playbooks) by name and one-line description. Call this first, then use_skill on the one that matches the task.",
                {}, []),
    _definition("use_skill", "Load a skill's full instructions by name (from list_skills) and follow them for the current task.",
                {"name": {"type": "string"}}, ["name"]),
    _definition("fetch_url", "Fetch a web page and return its readable text (HTML tags stripped). Use after web_search, or when the user gives a direct URL.",
                {"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 20000}}, ["url"]),
    _definition("web_search", "Search the web (no API key needed) and return titles/URLs/snippets. Use when the user asks to look something up or you need current information; follow up with fetch_url on the best result.",
                {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 6}}, ["query"]),
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