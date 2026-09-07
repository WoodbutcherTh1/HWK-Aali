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


_EXFIL_PATTERNS = (
    re.compile(r"os\.environ|environ\s*\[|environ\.copy|environ\.items", re.IGNORECASE),
    re.compile(r"process\.env", re.IGNORECASE),
    re.compile(r"\bprintenv\b", re.IGNORECASE),
    re.compile(r"\bgetenv\b", re.IGNORECASE),
    re.compile(r"\bsecrets\b\.", re.IGNORECASE),
    re.compile(r"\.env\b", re.IGNORECASE),
    re.compile(r"\$[A-Z][A-Z0-9_]{2,}"),
)


def _looks_like_secret_exfiltration(command: str) -> bool:
    """True for commands whose purpose is dumping env vars/credentials."""
    return any(pattern.search(command) for pattern in _EXFIL_PATTERNS)


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
    if _looks_like_secret_exfiltration(command):
        # Lesson (nx/npm 2025, Samsung 2023): commands that dump environment
        # variables or config files are how API keys leave a machine. Aali
        # never executes them, even "for debugging".
        raise FileAgentError(
            "This command would expose environment variables or credentials "
            "(os.environ / process.env / config dumps are blocked). If you need "
            "a specific setting, ask the user for the name of the value instead."
        )
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
    from file_agent import memory as _memory
    output = _memory.redact_secrets(output)  # defense in depth: nothing that looks like a credential leaves the sandbox
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


def generate_video(prompt: str, path: str, workspace_root: str | Path, *,
                   negative_prompt: str = "", num_frames: int = 16, steps: int = 25,
                   guidance_scale: float = 9.0, width: int = 256, height: int = 256,
                   fps: int = 8, seed: int | None = None) -> dict[str, Any]:
    """Generate a SHORT, LOW-RESOLUTION video clip from a text prompt with a
    local text-to-video model. Honest limitation, not a bug: this is a small
    open model on one consumer GPU, not a commercial video generator - expect
    a couple of seconds at low resolution, sometimes rough. Always mention
    this limitation to the user rather than presenting it as ad-quality video.

    Runs scripts/video_gen_tool.py in a subprocess (torch + diffusers stay
    out of the main agent process), same pattern as generate_image/analyze_video.
    """
    if not prompt or not prompt.strip():
        raise FileAgentError("prompt must be non-empty")
    if num_frames < 1 or steps < 1:
        raise FileAgentError("num_frames and steps must be at least 1")
    if width < 64 or height < 64:
        raise FileAgentError("width/height must be at least 64")
    root, target = _resolve(path, workspace_root)
    if target.suffix.lower() != ".mp4":
        raise FileAgentError("path must end in .mp4")
    if target.exists():
        raise FileAgentError(f"File already exists: {_relative(root, target)}; choose a different path")
    script = Path(__file__).resolve().parents[2] / "scripts" / "video_gen_tool.py"
    if not script.is_file():
        raise FileAgentError("video_gen_tool.py not found")
    command = [
        sys.executable, "-u", str(script), prompt,
        "--output", str(target), "--negative-prompt", negative_prompt,
        "--num-frames", str(num_frames), "--steps", str(steps),
        "--guidance", str(guidance_scale), "--width", str(width),
        "--height", str(height), "--fps", str(fps),
    ]
    if seed is not None:
        command += ["--seed", str(seed)]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800,  # first run downloads the model; generation itself is much slower than images
        )
    except subprocess.TimeoutExpired as exc:
        raise FileAgentError("Video generation timed out (first run downloads the model, and this is much slower than image generation - try again once it's cached)") from exc
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        result = {}
    if proc.returncode != 0 or "error" in result:
        message = result.get("message") if result else None
        raise FileAgentError(message or "Video generation failed: " + (proc.stderr or proc.stdout or "")[-500:])
    result["path"] = _relative(root, target)
    return result


EDIT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "media_edit.py"
MACHINE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "machine_ops.py"


def _run_tool_script(script: Path, args: list[str], timeout: int) -> dict[str, Any]:
    """Run one of the scripts/* tool CLIs and parse its final JSON line."""
    if not script.is_file():
        raise FileAgentError(f"{script.name} not found")
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(script), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FileAgentError(f"{script.name} timed out after {timeout}s") from exc
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        result = {}
    if proc.returncode != 0 or "error" in result:
        message = result.get("error") if result else None
        raise FileAgentError(message or f"{script.name} failed: " + (proc.stderr or proc.stdout or "")[-400:])
    return result


def edit_image(path: str, output: str, workspace_root: str | Path, *, op: str,
               width: int = 0, height: int = 0, left: int = 0, top: int = 0,
               deg: int = 0, factor: float = 1.0, radius: float = 2.0,
               text: str = "") -> dict[str, Any]:
    """Fast image edit with Pillow (resize, crop, rotate, flip, mirror, grayscale,
    brightness, contrast, saturation, blur, sharpen, border, watermark_text,
    thumbnail). Refuses to overwrite; write to a NEW output path."""
    if not op:
        raise FileAgentError("op is required")
    root, source = _resolve(path, workspace_root, must_exist=True)
    root, target = _resolve(output, workspace_root)
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise FileAgentError("image input must be .png/.jpg/.jpeg")
    if target.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise FileAgentError("output must end in .png, .jpg, or .jpeg")
    result = _run_tool_script(EDIT_SCRIPT, [
        "image", "--op", op, "--input", str(source), "--output", str(target),
        "--width", str(width), "--height", str(height), "--left", str(left),
        "--top", str(top), "--deg", str(deg), "--factor", str(factor),
        "--radius", str(radius), "--text", text,
    ], timeout=300)
    result["output"] = _relative(root, target)
    result["input"] = _relative(root, source)
    return result


def edit_video(path: str, output: str, workspace_root: str | Path, *, op: str,
               start: float = 0.0, end: float | None = None, duration: float | None = None,
               factor: float = 1.0, width: int = 0, height: int = 0,
               left: int = 0, top: int = 0, deg: int = 0,
               input2: str = "", position: str = "bottom_right") -> dict[str, Any]:
    """Fast video edit with ffmpeg (cut, trim, concat, gif, extract_audio,
    extract_frame, speed, volume, resize, crop, rotate, flip, mirror, fade,
    watermark_image). input2 is the second clip (concat) or overlay image
    (watermark_image). Refuses to overwrite; write to a NEW output path."""
    if not op:
        raise FileAgentError("op is required")
    root, source = _resolve(path, workspace_root, must_exist=True)
    root, target = _resolve(output, workspace_root)
    args = [
        "video", "--op", op, "--input", str(source), "--output", str(target),
        "--start", str(start), "--factor", str(factor), "--width", str(width),
        "--height", str(height), "--left", str(left), "--top", str(top),
        "--deg", str(deg), "--position", position,
    ]
    if end is not None:
        args += ["--end", str(end)]
    if duration is not None:
        args += ["--duration", str(duration)]
    if input2:
        root2, second = _resolve(input2, workspace_root, must_exist=True)
        args += ["--input2", str(second)]
    result = _run_tool_script(EDIT_SCRIPT, args, timeout=900)
    result["output"] = _relative(root, target)
    result["input"] = _relative(root, source)
    return result


def machine_ops(action: str, workspace_root: str | Path, *, target: str = "",
                engine: str = "auto", force: bool = False, name: str = "",
                pid: int | None = None, max_results: int = 30) -> dict[str, Any]:
    """Operate the owner's Windows machine: open apps/files/URLs, install or
    uninstall software (winget/npm), list and stop processes, read system
    stats. install/uninstall/kill_process are IRREVERSIBLE: they require
    force=true here AND explicit user confirmation (always_ask policy gates
    them server-side). Aali's own training runtimes (python/node) are
    protected from kill by name."""
    allowed = {"open", "install", "uninstall", "search_software",
               "list_processes", "kill_process", "system_info"}
    if action not in allowed:
        raise FileAgentError(f"action must be one of {sorted(allowed)}")
    if action in {"install", "uninstall"} and not force:
        raise FileAgentError(
            f"{action} changes the system permanently: set force=true only AFTER the "
            "user explicitly confirms in chat, and say what you are about to install first"
        )
    if action == "kill_process" and not force:
        raise FileAgentError(
            "kill_process is irreversible: set force=true only AFTER the user confirms "
            "the exact program to stop"
        )
    args = [action]
    timeout = 120
    if action in {"open", "install", "uninstall", "search_software"}:
        if not target.strip():
            raise FileAgentError(f"{action} needs a target")
        args += ["--target", target]
    if action in {"install", "uninstall"}:
        args += ["--engine", engine if engine in {"winget", "npm"} else "auto", "--force"]
        timeout = 900
    if action == "search_software" and engine in {"winget", "npm"}:
        args += ["--engine", engine]
    if action == "list_processes":
        if name:
            args += ["--name", name]
        args += ["--max-results", str(max_results)]
    if action == "kill_process":
        if not name and pid is None:
            raise FileAgentError("kill_process needs name or pid")
        if name:
            args += ["--name", name]
        if pid is not None:
            args += ["--pid", str(pid)]
        args += ["--force"]
    result = _run_tool_script(MACHINE_SCRIPT, args, timeout=timeout)
    result["action"] = action
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


def generate_emoji(prompt: str, path: str, workspace_root: str | Path, *,
                   emotion: str = "happy", palette: str = "yellow",
                   size: int = 256, accessory: str = "", text: str = "") -> dict[str, Any]:
    """Draw a custom emoji FROM SCRATCH (Pillow, CPU - instant even while
    the GPU trains). Emotion-driven faces with palettes and accessories,
    saved as a transparent PNG. `prompt` carries the user's description.
    """
    del prompt  # the structured args carry the design; prompt is for the log
    import subprocess
    import sys as _sys
    script = Path(__file__).resolve().parents[2] / "scripts" / "generate_emoji.py"
    out = str((workspace_root / path) if not Path(path).is_absolute() else path)
    completed = subprocess.run(
        [_sys.executable, str(script), "--out", out,
         "--emotion", emotion, "--palette", palette,
         "--size", str(size), "--accessory", accessory, "--text", text],
        capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise FileAgentError(f"emoji generation failed: {completed.stderr[-400:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def memory(action: str, workspace_root: str | Path, *,
           text: str = "", kind: str = "fact", topic: str = "",
           query: str = "", entry_id: str = "", match_text: str = "",
           limit: int = 50) -> dict[str, Any]:
    """Persistent long-term memory about the user (survives restarts).

    action=save    : store a user statement (decision/preference/fact/instruction)
    action=recall  : search what the user said before (newest first)
    action=forget  : remove entries (gated by the always_ask policy)
    action=summary : counts + latest entries

    Secrets pasted by the user are redacted automatically before storage.
    """
    from file_agent import memory as _memory
    valid_kinds = ("decision", "preference", "fact", "instruction")
    try:
        if action == "save":
            if not str(text).strip():
                raise _memory.MemoryError("save يحتاج نصًا: memory(action='save', text='...')")
            if kind not in valid_kinds:
                raise _memory.MemoryError(f"kind must be one of {valid_kinds}, got {kind!r}")
            return _memory.remember(str(text), kind=kind,
                                    topic=(topic or None), source="user")["result"]
        if action == "recall":
            return _memory.recall(query=query, topic=(topic or None), limit=limit)["result"]
        if action == "forget":
            kind_filter = kind if kind in valid_kinds else ""
            return _memory.forget(entry_id=entry_id, match_text=match_text,
                                  topic=(topic or None), kind=kind_filter)["result"]
        if action == "summary":
            return _memory.summary()["result"]
        raise _memory.MemoryError(
            f"action must be save/recall/forget/summary, got {action!r}")
    except _memory.MemoryError as exc:
        raise FileAgentError(str(exc)) from exc


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
    "generate_video": generate_video,
    "edit_image": edit_image,
    "edit_video": edit_video,
    "machine_ops": machine_ops,
    "memory": memory,
    "generate_emoji": generate_emoji,
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
    _definition("generate_video", "Generate a SHORT (a few seconds), LOW-RESOLUTION video clip from a text prompt using a small local text-to-video model. This is NOT comparable to commercial video generators (Sora/Veo/Runway) - always tell the user it is a rough local clip, never present it as ad-quality. Shares the GPU with any running Aali training.",
                {"prompt": {"type": "string"}, "path": {"type": "string"},
                 "negative_prompt": {"type": "string", "default": ""},
                 "num_frames": {"type": "integer", "minimum": 1, "default": 16},
                 "steps": {"type": "integer", "minimum": 1, "default": 25},
                 "width": {"type": "integer", "default": 256}, "height": {"type": "integer", "default": 256},
                 "fps": {"type": "integer", "default": 8},
                 "seed": {"type": "integer"}}, ["prompt", "path"]),
    _definition("edit_image", "Edit an existing image quickly (no AI model): resize, crop, rotate, flip, mirror, grayscale, brightness, contrast, saturation, blur, sharpen, border, watermark_text, thumbnail. Write to a NEW output path - overwriting is refused.",
                {"path": {"type": "string"}, "output": {"type": "string"}, "op": {"type": "string", "enum": ["resize", "crop", "rotate", "flip", "mirror", "grayscale", "brightness", "contrast", "saturation", "blur", "sharpen", "border", "watermark_text", "thumbnail"]},
                 "width": {"type": "integer", "default": 0}, "height": {"type": "integer", "default": 0},
                 "left": {"type": "integer", "default": 0}, "top": {"type": "integer", "default": 0},
                 "deg": {"type": "integer", "default": 0}, "factor": {"type": "number", "default": 1.0},
                 "radius": {"type": "number", "default": 2.0}, "text": {"type": "string", "default": ""}}, ["path", "output", "op"]),
    _definition("edit_video", "Edit an existing video quickly with ffmpeg: cut, trim, concat, gif, extract_audio, extract_frame, speed, volume, resize, crop, rotate, flip, mirror, fade, watermark_image. input2 = second clip (concat) or overlay PNG (watermark_image). Write to a NEW output path.",
                {"path": {"type": "string"}, "output": {"type": "string"}, "op": {"type": "string", "enum": ["cut", "trim", "concat", "gif", "extract_audio", "extract_frame", "speed", "volume", "resize", "crop", "rotate", "flip", "mirror", "fade", "watermark_image"]},
                 "start": {"type": "number", "default": 0.0}, "end": {"type": "number"}, "duration": {"type": "number"},
                 "factor": {"type": "number", "default": 1.0}, "width": {"type": "integer", "default": 0},
                 "height": {"type": "integer", "default": 0}, "left": {"type": "integer", "default": 0},
                 "top": {"type": "integer", "default": 0}, "deg": {"type": "integer", "default": 0},
                 "input2": {"type": "string", "default": ""}, "position": {"type": "string", "enum": ["top_left", "top_right", "bottom_left", "bottom_right"], "default": "bottom_right"}}, ["path", "output", "op"]),
    _definition("machine_ops", "Operate the owner's Windows PC: open apps/files/URLs (open), install/uninstall software via winget or npm (install/uninstall - IRREVERSIBLE: requires force=true AND explicit user confirmation in chat before use), search package names (search_software), list processes (list_processes), stop a program (kill_process - IRREVERSIBLE, needs force=true; Aali's own python/node runtimes are protected), or read system stats (system_info: GPU/RAM/disks/CPU).",
                {"action": {"type": "string", "enum": ["open", "install", "uninstall", "search_software", "list_processes", "kill_process", "system_info"]},
                 "target": {"type": "string", "default": ""}, "engine": {"type": "string", "enum": ["auto", "winget", "npm"], "default": "auto"},
                 "force": {"type": "boolean", "default": False}, "name": {"type": "string", "default": ""},
                 "pid": {"type": "integer"}, "max_results": {"type": "integer", "default": 30}}, ["action"]),
    _definition("memory", "Persistent long-term memory about THIS user that survives restarts and new conversations. save: store a durable user statement (kind: decision/preference/fact/instruction, optional topic) - use it whenever the user says 'from now on', 'always', 'never', 'remember that'. recall: search what the user told you before BEFORE saying you don't know. forget: remove entries (needs explicit user confirmation). summary: counts and latest entries. If a newer instruction contradicts an older one, follow the newest and tell the user what changed.",
                {"action": {"type": "string", "enum": ["save", "recall", "forget", "summary"]},
                 "text": {"type": "string", "default": ""},
                 "kind": {"type": "string", "enum": ["decision", "preference", "fact", "instruction"], "default": "fact"},
                 "topic": {"type": "string", "default": ""},
                 "query": {"type": "string", "default": ""},
                 "entry_id": {"type": "string", "default": ""},
                 "match_text": {"type": "string", "default": ""},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}, ["action"]),
    _definition("generate_emoji", "Generate a CUSTOM emoji from scratch (drawn with Pillow on CPU - instant, works even while the GPU trains): emotion-driven faces (happy/joy/love/sad/angry/surprised/wink/cool/sleepy/laughing), color palettes, accessories (party/crown/graduation), optional caption text. Saves a transparent PNG sticker.",
                {"prompt": {"type": "string", "description": "the user's description of the emoji"},
                 "path": {"type": "string"},
                 "emotion": {"type": "string", "enum": ["happy", "joy", "love", "sad", "angry", "surprised", "wink", "cool", "sleepy", "laughing"], "default": "happy"},
                 "palette": {"type": "string", "enum": ["yellow", "orange", "red", "green", "blue", "purple", "pink"], "default": "yellow"},
                 "size": {"type": "integer", "default": 256},
                 "accessory": {"type": "string", "enum": ["", "party", "crown", "graduation"], "default": ""},
                 "text": {"type": "string", "default": ""}}, ["prompt", "path"]),
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