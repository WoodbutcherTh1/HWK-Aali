"""hwk_paths — where HWK/Aali keeps its stuff, on EVERY operating system.

AGENTS.md says: *"Never hardcode absolute user paths into library code; use
hwk_paths.py"*. This is that module. It exists because three clients (آلي
Desktop, آلي ستوديو, آلي CLI) had each grown their own `%APPDATA%` snippet,
which happens to work on Windows and quietly scatters `~/AaliStudio` folders
around macOS and Linux.

Rules obeyed here:
* stdlib only (it is imported by frozen, CPU-only client builds),
* every directory is overridable by env for tests and odd installs,
* nothing is created until something actually needs it.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

#: A machine-owned data root (never inside the repo, never in the app dir).
DATA_ENV = "AALI_DATA_DIR"


def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def os_label() -> str:
    """Short, honest name of the host — used in logs and the portability check."""
    if is_windows():
        return "windows"
    if is_macos():
        return f"macos {platform.mac_ver()[0]}".strip()
    if is_linux():
        return f"linux {platform.release()}"
    return sys.platform or "unknown"


def home() -> Path:
    return Path(os.path.expanduser("~"))


def config_dir(app: str = "Aali") -> Path:
    """Per-user configuration for a client app.

    Windows ``%APPDATA%\\Aali``, macOS ``~/Library/Application Support/Aali``,
    Linux ``$XDG_CONFIG_HOME/aali`` (or ``~/.config/aali``) — the platform's
    own convention, so nothing lands somewhere surprising. ``AALI_CONFIG_DIR``
    overrides all of it (tests, portable installs).
    """
    override = os.getenv("AALI_CONFIG_DIR")
    if override:
        return Path(override)
    app = app.strip().strip("/\\") or "Aali"
    if is_windows():
        base = os.getenv("APPDATA") or str(home() / "AppData" / "Roaming")
        return Path(base) / app
    if is_macos():
        return home() / "Library" / "Application Support" / app
    base = os.getenv("XDG_CONFIG_HOME") or str(home() / ".config")
    return Path(base) / app.lower()


def data_root() -> Path:
    """The machine's shared data root: ``D:/hwk-data`` on the owner's PC.

    Everywhere else (a MacBook, Linux, CI) there is no D: drive, so it falls
    back to ``~/hwk-data`` instead of pretending a path exists.
    """
    override = os.getenv(DATA_ENV)
    if override:
        return Path(override)
    candidate = Path("D:/hwk-data")
    if is_windows() and candidate.exists():
        return candidate
    return home() / "hwk-data"


def repo_root() -> Path | None:
    """The HWK-Aali checkout, if this code is running inside one."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "file-agent" / "app.py").is_file():
            return parent
    return None


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_external(url: str) -> str:
    """Open a URL in the OS default browser — the cross-platform `startfile`.

    ``os.startfile`` exists only on Windows, so the Desktop client's
    "open this link" button raised AttributeError on a MacBook. Returns a short
    status string for the UI (``opened`` / ``no-opener`` / ``bad-url``).
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return "bad-url"
    if is_windows():
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
        return "opened"
    opener = "open" if is_macos() else "xdg-open"
    try:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return "opened"
    except (OSError, FileNotFoundError):
        return "no-opener"


def cloudflared_name() -> str:
    """The cloudflared binary name for THIS platform (share/tunnel feature)."""
    return "cloudflared.exe" if is_windows() else "cloudflared"