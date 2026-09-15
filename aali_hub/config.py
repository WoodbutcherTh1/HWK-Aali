"""Aali Hub configuration — env-var driven, no hardcoded paths.

Every value is overridable via environment variables; defaults are safe for
local development only. Secrets (JWT secret, brain token) have NO defaults:
the Hub refuses to start with real auth if they are missing, unless
``AALI_HUB_DEV=1`` opts into ephemeral development secrets.

The Hub targets a Linux VPS (Docker/systemd); log paths default to /var/log/aali
on Linux and ./hub_logs on Windows. Nothing here reads fixed drive letters.
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["HubConfig", "load_config"]


def _default_log_dir() -> Path:
    """Pick the default log directory per OS (no hardcoded drive letters)."""
    if sys.platform.startswith("win"):
        return Path.cwd() / "hub_logs"
    return Path("/var/log/aali")


@dataclass(frozen=True)
class HubConfig:
    """Immutable Hub configuration, built once at startup."""

    # server
    host: str = "127.0.0.1"
    port: int = 8080
    dev_mode: bool = False
    log_dir: Path = field(default_factory=_default_log_dir)
    db_path: Path = field(default_factory=lambda: Path("aali_hub.db"))

    # auth — secrets have no defaults (see load_config)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_sec: int = 60 * 60 * 24          # 24h
    brain_token: str = ""
    master_key: str = ""                               # protocol HMAC master
    update_signing_key: str = ""                       # update manifest signer

    # brain
    brain_url: str = "http://127.0.0.1:5055"
    brain_wake_timeout_sec: float = 30.0

    # wake-on-lan
    wol_mac: str = ""                                  # AA:BB:CC:DD:EE:FF
    wol_broadcast: str = "255.255.255.255"
    wol_port: int = 9

    # queue / rate limits (per user; external per-key limits in queue.py)
    daily_limit_free: int = 20
    daily_limit_pro: int = 500
    daily_limit_enterprise: int = 2000
    max_concurrent_per_user: int = 3
    user_requests_per_min: int = 100
    tool_calls_per_min: int = 10
    max_concurrent_tool_calls: int = 5
    circuit_breaker_cooldown_sec: float = 30.0

    # updates
    update_dir: Path = field(default_factory=lambda: Path("updates"))
    update_keep_versions: int = 3


def load_config(env: dict[str, str] | None = None) -> HubConfig:
    """Build a HubConfig from environment variables.

    In dev mode (``AALI_HUB_DEV=1``) missing secrets are replaced with
    ephemeral random values — fine for tests, never for production.
    Without dev mode, missing ``AALI_HUB_JWT_SECRET`` raises ``RuntimeError``.
    """
    source = os.environ if env is None else env

    def get(name: str, default: str = "") -> str:
        return source.get(name, default)

    def get_int(name: str, default: int) -> int:
        raw = source.get(name, "")
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    def get_float(name: str, default: float) -> float:
        raw = source.get(name, "")
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    dev = get("AALI_HUB_DEV") == "1"
    jwt_secret = get("AALI_HUB_JWT_SECRET")
    brain_token = get("AALI_BRAIN_TOKEN")
    master_key = get("AALI_PROTOCOL_MASTER_KEY")
    update_signing_key = get("AALI_UPDATE_SIGNING_KEY")
    if dev:
        # ephemeral per-process secrets; safe only for tests/dev
        jwt_secret = jwt_secret or secrets.token_hex(32)
        brain_token = brain_token or secrets.token_hex(16)
        master_key = master_key or secrets.token_hex(32)
        # NOTE: the update signing key deliberately has NO dev fallback —
        # update_server (HMAC mode) refuses empty keys and an ephemeral
        # publish key would sign artifacts no deployed Node could verify.
    elif not jwt_secret:
        raise RuntimeError(
            "AALI_HUB_JWT_SECRET is not set and AALI_HUB_DEV != 1 — "
            "refusing to start with guessable auth secrets"
        )

    return HubConfig(
        host=get("AALI_HUB_HOST", "127.0.0.1"),
        port=get_int("AALI_HUB_PORT", 8080),
        dev_mode=dev,
        log_dir=Path(get("AALI_HUB_LOG_DIR", str(_default_log_dir()))),
        db_path=Path(get("AALI_HUB_DB", "aali_hub.db")),
        jwt_secret=jwt_secret,
        jwt_algorithm=get("AALI_HUB_JWT_ALG", "HS256"),
        access_token_ttl_sec=get_int("AALI_HUB_TOKEN_TTL", 60 * 60 * 24),
        brain_token=brain_token,
        master_key=master_key,
        update_signing_key=update_signing_key,
        brain_url=get("AALI_BRAIN_URL", "http://127.0.0.1:5055").rstrip("/"),
        brain_wake_timeout_sec=get_float("AALI_BRAIN_WAKE_TIMEOUT", 30.0),
        wol_mac=get("AALI_WOL_MAC"),
        wol_broadcast=get("AALI_WOL_BROADCAST", "255.255.255.255"),
        wol_port=get_int("AALI_WOL_PORT", 9),
        daily_limit_free=get_int("AALI_DAILY_FREE", 20),
        daily_limit_pro=get_int("AALI_DAILY_PRO", 500),
        daily_limit_enterprise=get_int("AALI_DAILY_ENTERPRISE", 2000),
        max_concurrent_per_user=get_int("AALI_MAX_CONCURRENT", 3),
        user_requests_per_min=get_int("AALI_RATE_USER_RPM", 100),
        tool_calls_per_min=get_int("AALI_RATE_TOOL_RPM", 10),
        max_concurrent_tool_calls=get_int("AALI_RATE_TOOL_CONCURRENT", 5),
        circuit_breaker_cooldown_sec=get_float("AALI_CB_COOLDOWN", 30.0),
        update_dir=Path(get("AALI_UPDATE_DIR", "updates")),
        update_keep_versions=get_int("AALI_UPDATE_KEEP", 3),
    )
