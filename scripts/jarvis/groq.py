"""Groq API client: chat completions and Whisper speech-to-text.

Uses ``requests`` if available and falls back to stdlib ``urllib`` so the bot
can still answer on a venv where requests is missing. Nothing here logs the
API key or the text it sends.
"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shape depends on the venv
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

from .config import GroqConfig

TIMEOUT = 60

# Cloudflare (error 1010) rejects urllib's default "Python-urllib/x.y" agent on
# the multipart endpoint while allowing requests' own. Set a plain explicit UA
# on every urllib call so STT and chat behave the same either way.
USER_AGENT = "jarvis/1.0 (+aali)"

SYSTEM_PROMPT = (
    "أنت جارفيس، مساعد المالك. تتكلم العربية أولاً وباختصار. "
    "أنت سريع ودقيق وتذكر أن آلي هو العقل وأدواته على الحاسوب. "
    "أجب بجملة أو جملتين ما لم يُطلب تفصيل. لا تكرر، ولا تشرح خطواتك الداخلية."
)


class GroqError(RuntimeError):
    """Any failure talking to Groq (network, auth, or bad response)."""


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    if requests is not None:
        try:
            resp = requests.post(url, headers=headers, data=body, timeout=TIMEOUT)
        except Exception as exc:  # network, DNS, TLS...
            raise GroqError(f"network error: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise GroqError(f"HTTP {resp.status_code}: {_short(resp.text)}")
        try:
            return resp.json()
        except Exception as exc:
            raise GroqError("bad JSON from Groq") from exc

    req = urllib.request.Request(
        url, data=body, headers={**headers, "User-Agent": USER_AGENT}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise GroqError(f"HTTP {exc.code}: {_short(detail)}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GroqError(f"network error: {type(exc).__name__}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GroqError("bad JSON from Groq") from exc


def _short(text: str, limit: int = 160) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text[:limit]


def complete(
    cfg: GroqConfig,
    message: str,
    history: list[dict[str, str]] | None = None,
    system: str = SYSTEM_PROMPT,
    temperature: float = 0.6,
    max_tokens: int = 600,
) -> str:
    """One completion with the sampling knobs exposed. Raises GroqError.

    Split out of :func:`chat` because the intent router needs ``temperature=0``
    and a small token ceiling on EVERY message: it is a classifier, and a
    creative classifier is a classifier that eventually routes a shutdown to
    the wrong machine.
    """
    data = _post_json(
        f"{cfg.base_url}/chat/completions",
        {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
        {
            "model": cfg.chat_model,
            "messages": _messages(message, history, system),
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    )
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError("unexpected completion shape") from exc


def chat(
    cfg: GroqConfig,
    message: str,
    history: list[dict[str, str]] | None = None,
    system: str = SYSTEM_PROMPT,
) -> str:
    """Free-form conversation. Raises GroqError on any failure."""
    return complete(cfg, message, history=history, system=system)


# A model asked for JSON will sometimes wrap it in a fence or a sentence. The
# first {...} span is the only honest place to look, and anything else is a
# failure the caller can fall back from -- never a guess.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(raw: str) -> dict[str, Any]:
    """Pull one JSON object out of a model reply. Raises GroqError."""
    text = _FENCE.sub("", (raw or "").strip())
    if not text:
        raise GroqError("empty reply where JSON was expected")
    candidate = text
    if not candidate.startswith("{"):
        m = _OBJ.search(text)
        if not m:
            raise GroqError("no JSON object in the reply")
        candidate = m.group(0)
    try:
        data = json.loads(candidate)
    except ValueError as exc:
        raise GroqError(f"reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GroqError("JSON reply was not an object")
    return data


def chat_json(
    cfg: GroqConfig,
    message: str,
    system: str,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
    max_tokens: int = 300,
) -> dict[str, Any]:
    """One completion that must come back as a JSON object.

    ``model`` overrides the configured chat model so the router can run on a
    smaller, faster one. Raises GroqError on anything unusable -- the caller
    treats that as "no decision" rather than "some decision".
    """
    data = _post_json(
        f"{cfg.base_url}/chat/completions",
        {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
        {
            "model": model or cfg.chat_model,
            "messages": _messages(message, history, system),
            "temperature": 0.0,
            "max_tokens": max_tokens,
        },
    )
    try:
        raw = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError("unexpected completion shape") from exc
    return parse_json_object(raw or "")


def _messages(
    message: str, history: list[dict[str, str]] | None, system: str
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in (history or [])[-8:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def _post_multipart(
    url: str,
    headers: dict[str, str],
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
) -> dict[str, Any]:
    boundary = f"----jarvis{uuid.uuid4().hex}"
    sep = f"--{boundary}\r\n".encode()
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(sep)
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    parts.append(sep)
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    hdrs = dict(headers)
    hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    hdrs["User-Agent"] = USER_AGENT

    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise GroqError(f"HTTP {exc.code}: {_short(detail)}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GroqError(f"network error: {type(exc).__name__}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GroqError("bad JSON from Groq") from exc


def transcribe(
    cfg: GroqConfig,
    audio: bytes,
    filename: str = "voice.ogg",
    language: str | None = None,
) -> str:
    """Transcribe a Telegram voice note with Whisper. Raises GroqError."""
    fields = {"model": cfg.stt_model, "response_format": "json"}
    if language:
        fields["language"] = language
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    data = _post_multipart(
        f"{cfg.base_url}/audio/transcriptions",
        {"Authorization": f"Bearer {cfg.api_key}"},
        fields,
        "file",
        filename,
        audio,
        ctype,
    )
    text = (data.get("text") or "").strip()
    if not text:
        raise GroqError("transcription came back empty")
    return text


def transcribe_file(cfg: GroqConfig, path: str | Path) -> str:
    p = Path(path)
    return transcribe(cfg, p.read_bytes(), filename=p.name)