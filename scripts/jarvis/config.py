"""Configuration loading for Jarvis.

Everything secret lives OUTSIDE the repo, in a config dir that defaults to
``~/.aali/jarvis`` and can be redirected with ``AALI_JARVIS_CONFIG``.

Files:
    groq_key.txt   the Groq API key, one line
    telegram.json  {"bot_token": "...", "owner_chat_id": 6027532184}
    machines.json  the pc/mac registry (see machines.py)

Nothing in this module ever logs a secret value.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# The only chat allowed to talk to Jarvis. The brief pins this number.
OWNER_CHAT_ID = 6027532184

DEFAULT_CONFIG_DIR = Path.home() / ".aali" / "jarvis"

_GROQ_URL = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b"
_STT_MODEL = "whisper-large-v3"


def config_dir() -> Path:
    """Directory holding the config files (env-overridable for tests)."""
    override = os.environ.get("AALI_JARVIS_CONFIG")
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_DIR


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    owner_chat_id: int


@dataclass(frozen=True)
class GroqConfig:
    api_key: str
    chat_model: str = _DEFAULT_MODEL
    stt_model: str = _STT_MODEL
    base_url: str = _GROQ_URL


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def load_groq(cfg_dir: Path | None = None) -> GroqConfig:
    d = cfg_dir or config_dir()
    key_file = d / "groq_key.txt"
    if not key_file.exists():
        raise FileNotFoundError(f"missing {key_file}")
    api_key = _read(key_file)
    if not api_key:
        raise ValueError(f"{key_file} is empty")
    return GroqConfig(
        api_key=api_key,
        chat_model=os.environ.get("AALI_JARVIS_MODEL", _DEFAULT_MODEL),
        stt_model=os.environ.get("AALI_JARVIS_STT_MODEL", _STT_MODEL),
    )


def load_telegram(cfg_dir: Path | None = None) -> TelegramConfig:
    d = cfg_dir or config_dir()
    f = d / "telegram.json"
    if not f.exists():
        raise FileNotFoundError(f"missing {f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    token = (data.get("bot_token") or "").strip()
    if not token:
        raise ValueError(f"{f} has no bot_token")
    chat_id = int(data.get("owner_chat_id") or OWNER_CHAT_ID)
    return TelegramConfig(bot_token=token, owner_chat_id=chat_id)


def ensure_config_dir() -> Path:
    """Create the config dir with owner-only permissions."""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass  # not fatal (Windows / exotic mounts)
    return d