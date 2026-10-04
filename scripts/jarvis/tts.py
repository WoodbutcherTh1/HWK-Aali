"""Text to speech: cloned voice (or espeak-ng) -> WAV -> OGG/Opus voice note.

Telegram voice messages must be OGG/Opus, so a WAV alone is not enough.

Two speech sources, tried in this order:

1. **The owner's cloned voice** — the PC hosts XTTS-v2 (``studio_api.py`` on
   :5082) because the Pi has 905 MB of RAM and no torch and simply cannot. The
   Pi POSTs the text and gets a WAV back, then runs the same ffmpeg step below.
   The PC is reached over the LAN behind a shared-secret token; without it the
   endpoint would let any device on the WiFi speak in the owner's voice.
2. **espeak-ng, locally** — the fallback when the PC is asleep, rebooting, or
   the voice server is down. Robot voice, but it always answers.

The owner asked for one rule: the bot speaks ENGLISH only, while the report
text stays Arabic. So the remote call always passes ``lang="en"`` no matter
what script the text is in.

Both local tools live in ``~/bin`` (installed without root) and espeak needs
its own libs in ``~/lib`` plus ``ESPEAK_DATA_PATH``. :func:`build_env`
assembles that environment so every subprocess in this module is
self-sufficient — a systemd unit does not have to carry the variables itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HOME = Path.home()
BIN_DIR = HOME / "bin"
LIB_DIR = HOME / "lib"
DATA_DIR = BIN_DIR / "espeak-ng-data"

Runner = Callable[[list[str], dict[str, Any], int], tuple[int, str]]

# The owner's cloned-voice server on the PC. Empty disables the remote source
# and leaves espeak as the only voice.
VOICE_CLONE = os.environ.get("AALI_VOICE_CLONE", "arabic_male").strip()
VOICE_TOKEN_FILE = HOME / ".aali" / "jarvis" / "voice_token.txt"
# The owner asked for English speech regardless of the report's script.
VOICE_LANG = "en"
# XTTS on CPU measured 16-29s per line; a cold model load is ~140s.
VOICE_TIMEOUT = int(os.environ.get("AALI_VOICE_TIMEOUT", "180"))

# What actually produced the last clip: "clone" or "espeak". Read by the
# caller for the log; never a secret.
LAST_SOURCE = ""

VOICE_PORT = int(os.environ.get("AALI_VOICE_PORT", "5082"))


def discover_voice_api() -> str:
    """Where the PC voice server lives.

    Order: the explicit ``AALI_VOICE_API`` env (the systemd unit declares it,
    so it is greppable), else the ``pc`` machine's IP in machines.json. The
    fallback exists because a DHCP change would otherwise leave the env var
    stale and quietly downgrade every reply to the robot voice.
    """
    explicit = os.environ.get("AALI_VOICE_API", "").strip().rstrip("/")
    if explicit:
        return explicit
    try:
        from .config import config_dir

        data = json.loads((config_dir() / "machines.json").read_text(encoding="utf-8"))
        machines = data.get("machines", data)
        pc = machines.get("pc") or {}
        ip = str(pc.get("ip", "")).strip()
        if ip:
            return f"http://{ip}:{VOICE_PORT}"
    except Exception:
        pass
    return ""


VOICE_API = discover_voice_api()


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
    """Any way to speak? Either the clone server or local espeak + ffmpeg.

    ffmpeg is required in BOTH paths (Telegram wants OGG/Opus, the clone server
    returns WAV), so a missing ffmpeg means no voice at all.
    """
    if not ffmpeg_path():
        return False
    return bool(clone_available() or espeak_path())


def clone_available() -> bool:
    """Is a clone endpoint configured AND its token present?"""
    return bool(VOICE_API) and VOICE_TOKEN_FILE.exists()


def clone_token() -> str:
    return VOICE_TOKEN_FILE.read_text(encoding="utf-8").strip()


def fetch_clone_wav(text: str, timeout: int | None = None) -> bytes:
    """Ask the PC to speak ``text`` in the cloned voice; return WAV bytes.

    Raises on any failure — the caller falls back to espeak rather than
    dropping the reply, because a text answer always beats no answer.
    """
    if not clone_available():
        raise TTSError("no clone voice endpoint configured")
    body = json.dumps(
        {"text": text, "lang": VOICE_LANG, "voice": VOICE_CLONE}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{VOICE_API}/api/synthesize",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Aali-Voice-Token": clone_token(),
            "User-Agent": "jarvis/1.0 (+aali)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout or VOICE_TIMEOUT) as resp:
        data = resp.read()
    if not data.startswith(b"RIFF"):
        raise TTSError("clone server did not return WAV")
    return data


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
    allow_clone: bool = True,
) -> Path:
    """Render ``text`` to an OGG/Opus file and return its path.

    Prefers the owner's cloned voice and falls back to local espeak. Raises
    TTSError when NOTHING can speak; the caller decides whether that means
    "send text only".

    ``allow_clone=False`` forces the local engine. The caller uses it when the
    text is Arabic: the clone is an ENGLISH voice, so feeding it Arabic script
    would produce noise, whereas espeak has a real Arabic voice.
    """
    global LAST_SOURCE

    text = (text or "").strip()
    if not text:
        raise TTSError("nothing to say")

    espeak = espeak_path()
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise TTSError("ffmpeg not installed (run the tools bootstrap)")
    if not espeak and not clone_available():
        raise TTSError("no voice source (espeak-ng missing, no clone endpoint)")

    run = runner or _run
    env = build_env()

    tmpdir = Path(tempfile.mkdtemp(prefix="jarvis-tts-"))
    wav = tmpdir / "out.wav"
    ogg = Path(out_path) if out_path else (tmpdir / "out.ogg")

    # 1) the cloned voice, when the PC is awake and answering
    wav_ready = False
    if clone_available() and allow_clone:
        try:
            wav.write_bytes(fetch_clone_wav(text))
            wav_ready = wav.exists() and wav.stat().st_size > 44
        except (TTSError, OSError, urllib.error.URLError, ValueError):
            wav_ready = False   # PC asleep / server down -> espeak below
        LAST_SOURCE = "clone" if wav_ready else ""

    # 2) local espeak fallback
    if not wav_ready:
        if not espeak:
            raise TTSError("clone voice unreachable and espeak-ng not installed")
        chosen = voice or pick_voice(text)
        code, out = run([espeak, "-v", chosen, "-w", str(wav), text], env, 60)
        if code != 0 or not wav.exists():
            raise TTSError(f"espeak failed ({code}): {out.strip()[-120:]}")
        LAST_SOURCE = "espeak"

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