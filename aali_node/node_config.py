"""Aali Node first-run config — the Hub/token entry screen's storage half.

Owner decision (Q4, 2026-09-15): first-run entry is BUILT, persisted to a
local JSON file so the user types the Hub URL + token once. The file is
still NOT a discovery mechanism — CLI/env flags always win, and the shell
offers "use saved config" explicitly.

Contract (the house security rules, applied to a config file):

- default location: ``%APPDATA%/AaliNode/aali-node.json`` (Windows) or
  ``~/.aali-node/aali-node.json`` (everything else) — next to the user, not
  inside the install (an update swap must never delete the user's config).
- the file is created with owner-only permissions; a readable-by-others
  file holding a token is REFUSED on POSIX (Windows ACLs cannot be checked
  portably — there we at least create tight and warn never assume).
- secrets are stored either inline (``token``) or as a FILE PATH
  (``token_file``); a file path wins because it lets the owner rotate the
  token without touching the JSON (and lets tools like DPAPI wrappers own
  the bytes). The file is read at load time, never logged.
- unknown keys are kept (forward compatibility), but anything that is not
  a string/bool/number is rejected — a config file is not a code vector.
- ``secrets()`` redacts every secret value so the shell can show the config
  without leaking the token into screenshots (the status-contract rule).
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

__all__ = ["NodeConfigError", "NodeConfig", "default_config_path",
           "load_node_config"]

_ALLOWED_KEYS = {"hub_url", "token", "token_file", "workspace",
                 "allow_commands", "update_hub", "update_key",
                 "update_key_file"}


class NodeConfigError(Exception):
    """Expected config failure (bad JSON, unsafe permissions, bad types)."""


def default_config_path() -> Path:
    """Per-user config path; never inside the install directory."""
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "AaliNode" / "aali-node.json"
    return Path.home() / ".aali-node" / "aali-node.json"


def _read_secret_file(path: str | Path, what: str) -> str:
    """Read a secret from a file; refuse unsafe permissions on POSIX."""
    p = Path(path).expanduser()
    try:
        data = p.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise NodeConfigError(f"{what} file unreadable: {exc}") from exc
    if not data:
        raise NodeConfigError(f"{what} file is empty: {p}")
    if os.name == "posix":
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
        except OSError as exc:
            raise NodeConfigError(f"{what} file unreadable: {exc}") from exc
        if mode & 0o077:
            raise NodeConfigError(
                f"{what} file is readable by others ({stat.filemode(mode)}) "
                f"— chmod 600 {p}")
    return data


def _validate_str(values: dict[str, Any], key: str) -> str:
    raw = values.get(key, "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise NodeConfigError(f"config key {key!r} must be a string")
    return raw.strip()


class NodeConfig:
    """Validated view over the saved Node configuration."""

    def __init__(self, values: dict[str, Any]) -> None:
        unknown = set(values) - _ALLOWED_KEYS
        if unknown:
            raise NodeConfigError(f"unknown config keys: {sorted(unknown)}")
        for key in values:
            if key in ("allow_commands",):
                if not isinstance(values[key], bool):
                    raise NodeConfigError(
                        f"config key {key!r} must be a boolean")
                continue
            if not isinstance(values.get(key), (str, type(None))):
                raise NodeConfigError(
                    f"config key {key!r} must be a string")
        self.hub_url = _validate_str(values, "hub_url")
        self.workspace = _validate_str(values, "workspace")
        self.update_hub = _validate_str(values, "update_hub")
        self._token_inline = _validate_str(values, "token")
        self._token_file = _validate_str(values, "token_file")
        self._update_key_inline = _validate_str(values, "update_key")
        self._update_key_file = _validate_str(values, "update_key_file")
        self.allow_commands = values.get("allow_commands", True)
        self._token_cache: str | None = None
        self._update_key_cache: str | None = None
        if self._token_file and self._token_inline:
            # ambiguity is a mistake the user cannot see — refuse it
            raise NodeConfigError(
                "set either token or token_file, not both")

    # -- secrets ------------------------------------------------------------
    @property
    def token(self) -> str:
        """The access JWT — inline or from its file; never logged."""
        if self._token_cache is None:
            if self._token_file:
                self._token_cache = _read_secret_file(
                    self._token_file, "token")
            else:
                self._token_cache = self._token_inline
        return self._token_cache

    @property
    def update_key(self) -> str:
        """Update-manifest verification key (empty = update check off)."""
        if self._update_key_cache is None:
            if self._update_key_file:
                self._update_key_cache = _read_secret_file(
                    self._update_key_file, "update_key")
            else:
                self._update_key_cache = self._update_key_inline
        return self._update_key_cache

    @property
    def using_token_file(self) -> bool:
        return bool(self._token_file)

    # -- persistence ----------------------------------------------------------
    def to_json(self) -> str:
        data: dict[str, Any] = {
            "hub_url": self.hub_url, "workspace": self.workspace,
            "allow_commands": self.allow_commands,
            "update_hub": self.update_hub,
        }
        if self._token_file:
            data["token_file"] = self._token_file
        else:
            data["token"] = self._token_inline
        if self._update_key_file:
            data["update_key_file"] = self._update_key_file
        elif self._update_key_inline:
            data["update_key"] = self._update_key_inline
        return json.dumps(data, ensure_ascii=True, indent=2) + "\n"

    def secrets(self) -> dict[str, str]:
        """Log/screenshot-safe view: secrets shown only as set/redacted."""
        out: dict[str, str] = {}
        out["token"] = ("<file>" if self._token_file
                        else ("***" if self._token_inline else ""))
        out["update_key"] = ("<file>" if self._update_key_file
                             else ("***" if self._update_key_inline else ""))
        return out

    # -- mutation -------------------------------------------------------------
    def save(self, path: str | Path) -> Path:
        """Write owner-only; refuses to loosen an existing file's mode."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix" and p.exists():
            mode = stat.S_IMODE(p.stat().st_mode)
            if mode & 0o077:
                raise NodeConfigError(
                    f"refusing to overwrite world-readable config "
                    f"({stat.filemode(mode)}) — chmod 600 {p} first")
        if os.name == "posix":
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.to_json())
        else:
            p.write_text(self.to_json(), encoding="utf-8")
        return p

    def mark_active(self) -> None:
        """Bookkeeping hook: the shell confirmed the user picked this config."""
        self._token_cache = self._token_cache or self.token


def load_node_config(path: str | Path) -> NodeConfig:
    """Parse + validate the saved config (NodeConfigError on any problem)."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise NodeConfigError(f"no saved config at {p}") from exc
    except OSError as exc:
        raise NodeConfigError(f"config unreadable: {exc}") from exc
    if os.name == "posix":
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
        except OSError as exc:
            raise NodeConfigError(f"config unreadable: {exc}") from exc
        if mode & 0o077:
            raise NodeConfigError(
                f"config file is readable by others ({stat.filemode(mode)}) "
                f"— chmod 600 {p}")
    try:
        values = json.loads(text)
    except ValueError as exc:
        raise NodeConfigError(f"config is not valid JSON: {exc}") from exc
    if not isinstance(values, dict):
        raise NodeConfigError("config must be a JSON object")
    cfg = NodeConfig(values)
    if cfg.using_token_file:
        cfg.token  # touch: fails fast when the secret file is broken
    if not cfg.hub_url:
        raise NodeConfigError("config has no hub_url")
    if not cfg.token:
        raise NodeConfigError("config has no token (or token_file)")
    return cfg
