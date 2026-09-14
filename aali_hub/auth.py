"""Aali Hub authentication — JWT minting/verification + brain token check.

HS256 JWTs. Uses PyJWT when importable; otherwise a small standards-compliant
stdlib implementation (base64url + HMAC-SHA256) produces and verifies the
exact same token shape, so tokens are portable across environments. Claims:

    sub  — user id          role — canonical role string
    kind — "access" | "reset"      iat/exp/jti — standard

The brain token is a static shared secret (env ``AALI_BRAIN_TOKEN``);
presentation is checked in constant time. Email-verification/reset email
delivery is intentionally NOT here (provider decision pending with the owner);
the token mechanics are.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

try:
    import jwt as _pyjwt
    _HAS_PYJWT = True
except ImportError:
    _HAS_PYJWT = False

__all__ = ["AuthError", "mint_token", "verify_token", "check_brain_token"]

_KINDS = ("access", "reset")


class AuthError(Exception):
    """Raised for invalid or expired tokens (never leaks why beyond kind)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _hmac_sign(signing_input: bytes, secret: str | bytes) -> bytes:
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, signing_input, hashlib.sha256).digest()


def _stdlib_encode(claims: dict[str, Any], secret: str | bytes) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (_b64url(json.dumps(header, separators=(",", ":"),
                                         sort_keys=True).encode("utf-8"))
                     + "." +
                     _b64url(json.dumps(claims, separators=(",", ":"),
                                        sort_keys=True).encode("utf-8")))
    return signing_input + "." + _b64url(_hmac_sign(signing_input.encode("ascii"), secret))


def _stdlib_decode(token: str, secret: str | bytes) -> dict[str, Any]:
    try:
        head_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{head_b64}.{payload_b64}".encode("ascii")
        if not hmac.compare_digest(_b64url(_hmac_sign(signing_input, secret)),
                                   sig_b64):
            raise AuthError("invalid", "signature mismatch")
        header = json.loads(_b64url_decode(head_b64))
        if header.get("alg") != "HS256":
            raise AuthError("invalid", f"algorithm {header.get('alg')!r} refused")
        claims = json.loads(_b64url_decode(payload_b64))
    except AuthError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise AuthError("invalid", "malformed token") from exc
    return claims


def mint_token(user_id: str, role: str, secret: str | bytes, *,
               ttl_sec: int, kind: str = "access",
               now: float | None = None) -> str:
    """Mint a signed JWT for *user_id* (kind: access or reset)."""
    if kind not in _KINDS:
        raise AuthError("invalid", f"unknown token kind {kind!r}")
    current = time.time() if now is None else now
    claims = {
        "sub": user_id,
        "role": role,
        "kind": kind,
        "iat": int(current),
        "exp": int(current + ttl_sec),
        "jti": uuid.uuid4().hex,
    }
    if _HAS_PYJWT:
        return _pyjwt.encode(claims, secret, algorithm="HS256")  # type: ignore[no-any-return]
    return _stdlib_encode(claims, secret)


def verify_token(token: str, secret: str | bytes, *,
                 kind: str = "access",
                 now: float | None = None) -> dict[str, Any]:
    """Verify signature + expiry (+ kind). Returns claims or raises AuthError."""
    if _HAS_PYJWT:
        try:
            claims = _pyjwt.decode(token, secret, algorithms=["HS256"])  # type: ignore[no-any-return]
        except _pyjwt.ExpiredSignatureError as exc:  # type: ignore[attr-defined]
            raise AuthError("expired", "token expired") from exc
        except _pyjwt.InvalidTokenError as exc:  # type: ignore[attr-defined]
            raise AuthError("invalid", "token rejected") from exc
    else:
        claims = _stdlib_decode(token, secret)
        current = time.time() if now is None else now
        if float(claims.get("exp", 0)) < current:
            raise AuthError("expired", "token expired")
    if claims.get("kind") != kind:
        raise AuthError("invalid", f"expected a {kind} token")
    if not claims.get("sub"):
        raise AuthError("invalid", "token has no subject")
    return claims


def check_brain_token(presented: str | None, configured: str | None) -> bool:
    """Constant-time comparison of the presented brain token."""
    if not presented or not configured:
        return False
    return hmac.compare_digest(presented.encode("utf-8"),
                               configured.encode("utf-8"))


def new_reset_token(user_id: str, secret: str | bytes, *,
                    ttl_sec: int = 1800) -> str:
    """Mint a short-lived password-reset token (30 min default)."""
    return mint_token(user_id, "", secret, ttl_sec=ttl_sec, kind="reset")


def new_email_secret() -> str:
    """Random secret for verify/reset flows (shown once, stored hashed)."""
    return secrets.token_urlsafe(24)
