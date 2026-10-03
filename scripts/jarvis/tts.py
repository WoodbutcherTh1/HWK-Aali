"""Text to speech: espeak-ng -> WAV -> OGG/Opus voice note.

Telegram voice messages must be OGG/Opus, so a WAV alone is not enough.

Both tools live in ``~/bin`` (installed without root) and espeak needs its own
libs in ``~/lib`` plus ``ESPEAK_DATA_PATH``. :func:`build_env` assembles that
environment so every subprocess in this module is self-sufficient — a systemd
unit does not have to carry the variables itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

HOME = Path.home()
BIN_DIR = HOME / "bin"
LIB_DIR = HOME / "lib"
DATA_DIR = BIN_DIR / "espeak-ng-data"

Runner = Callable[[list[str], dict[str, Any], int], tuple[int, str]]


class TTSError(RuntimeError):
    """Speech could not be produced."""


def build_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{LIB_DIR}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}"
    env["ESPEAK_DATA_PATH"] = str(DATA_DIR)
    return env


def espeak_path() -> str | None:
    return shutil.which("espeak-ng", path=str(BIN_DIR)) or shutil.which("espeak-ng")


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg", path=str(BIN_DIR)) or shutil.which("ffmpeg")


def available() -> bool:
    """Both tools present? Voice replies need both."""
    return bool(espeak_path() and ffmpeg_path())


def _run(argv: list[str], env: dict[str, str], timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, env=env, timeout=timeout, errors="replace"
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except FileNotFoundError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# Arabic text needs the Arabic voice; Latin text reads badly through it.
def pick_voice(text: str, arabic: str = "ar", english: str = "en") -> str:
    has_arabic = any("\u0600" <= ch <= "\u06ff" for ch in text)
    return arabic if has_arabic else english


def synthesize(
    text: str,
    out_path: str | Path | None = None,
    runner: Runner | None = None,
    voice: str | None = None,
) -> Path:
    """Render ``text`` to an OGG/Opus file and return its path.

    Raises TTSError when a tool is missing or the pipeline fails. The caller
    decides whether that means "send text only".
    """
    text = (text or "").strip()
    if not text:
        raise TTSError("nothing to say")

    espeak = espeak_path()
    ffmpeg = ffmpeg_path()
    if not espeak:
        raise TTSError("espeak-ng not installed (run the tools bootstrap)")
    if not ffmpeg:
        raise TTSError("ffmpeg not installed (run the tools bootstrap)")

    run = runner or _run
    env = build_env()
    chosen = voice or pick_voice(text)

    tmpdir = Path(tempfile.mkdtemp(prefix="jarvis-tts-"))
    wav = tmpdir / "out.wav"
    ogg = Path(out_path) if out_path else (tmpdir / "out.ogg")

    code, out = run([espeak, "-v", chosen, "-w", str(wav), text], env, 60)
    if code != 0 or not wav.exists():
        raise TTSError(f"espeak failed ({code}): {out.strip()[-120:]}")

    code, out = run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(wav),
            "-c:a", "libopus", "-b:a", "32k",
            "-ar", "48000", "-ac", "1",
            str(ogg),
        ],
        env,
        60,
    )
    if code != 0 or not ogg.exists():
        raise TTSError(f"ffmpeg failed ({code}): {out.strip()[-120:]}")

    if not out_path:
        # Caller wants bytes later; keep the file but tell them the temp dir.
        pass
    return ogg


def synthesize_bytes(text: str, runner: Runner | None = None) -> bytes:
    path = synthesize(text, runner=runner)
    try:
        return Path(path).read_bytes()
    finally:
        Path(path).unlink(missing_ok=True)
        try:
            Path(path).parent.rmdir()
        except OSError:
            pass