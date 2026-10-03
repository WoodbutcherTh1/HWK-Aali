"""Minimal Telegram Bot API client.

Plain long polling with an HTTP session — no async runtime, no heavy
framework, so it runs comfortably on a 1GB Raspberry Pi.

The base URL is injectable, which is what makes the whole bot testable
end-to-end against a local mock server with no token and no network.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

try:  # pragma: no cover
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_BASE = "https://api.telegram.org"
TIMEOUT = 60
POLL_TIMEOUT = 25


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str,
        owner_chat_id: int,
        base_url: str = DEFAULT_BASE,
        timeout: int = TIMEOUT,
    ) -> None:
        self.token = token
        self.owner_chat_id = int(owner_chat_id)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._offset: int | None = None

    # ---------------------------------------------------------------- core
    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._url(method)
        data = (payload or {}).copy()
        if requests is not None:
            try:
                resp = requests.post(url, json=data, timeout=self.timeout)
            except Exception as exc:
                raise TelegramError(f"network error: {type(exc).__name__}") from exc
            body = _parse(resp.text)
            if not body.get("ok"):
                raise TelegramError(f"{method}: {body.get('description', 'unknown error')}")
            return body.get("result") or {}

        encoded = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=encoded, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(detail)
                desc = body.get("description", "")
            except Exception:
                desc = ""
            raise TelegramError(f"{method}: HTTP {exc.code} {desc}".strip()) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TelegramError(f"network error: {type(exc).__name__}") from exc
        body = _parse(raw.decode("utf-8", "replace"))
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'unknown error')}")
        return body.get("result") or {}

    # ---------------------------------------------------------------- sends
    def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        return self.call("sendMessage", payload)

    def send_voice(
        self, chat_id: int, ogg_path: str | Path, caption: str | None = None
    ) -> dict:
        """Upload an OGG/Opus file as a real voice note."""
        p = Path(ogg_path)
        fieldname, filename, filedata, ctype = "voice", p.name, p.read_bytes(), "audio/ogg"
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        return self._upload("sendVoice", fields, fieldname, filename, filedata, ctype)

    def send_document(
        self, chat_id: int, path: str | Path, caption: str | None = None
    ) -> dict:
        p = Path(path)
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        ctype = "application/octet-stream"
        return self._upload("sendDocument", fields, "document", p.name, p.read_bytes(), ctype)

    def _upload(
        self,
        method: str,
        fields: dict[str, str],
        fieldname: str,
        filename: str,
        data: bytes,
        ctype: str,
    ) -> dict:
        boundary = f"----jarvis{uuid.uuid4().hex}"
        sep = f"--{boundary}\r\n".encode()
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(sep)
            parts.append(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            parts.append(f"{value}\r\n".encode())
        parts.append(sep)
        parts.append(
            f'Content-Disposition: form-data; name="{fieldname}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        parts.append(data)
        parts.append(f"\r\n--{boundary}--\r\n".encode())

        req = urllib.request.Request(
            self._url(method),
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise TelegramError(f"{method}: HTTP {exc.code} {detail[:120]}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TelegramError(f"network error: {type(exc).__name__}") from exc
        body = _parse(raw.decode("utf-8", "replace"))
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'unknown error')}")
        return body.get("result") or {}

    def send_typing(self, chat_id: int) -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except TelegramError:
            pass  # cosmetic

    # -------------------------------------------------------------- polling
    def get_updates(self, timeout: int = POLL_TIMEOUT) -> list[dict]:
        params: dict[str, Any] = {"timeout": timeout}
        if self._offset is not None:
            params["offset"] = self._offset
        url = f"{self._url('getUpdates')}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=timeout + 15) as resp:
                raw = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise TelegramError(f"network error: {type(exc).__name__}") from exc
        body = _parse(raw.decode("utf-8", "replace"))
        if not body.get("ok"):
            raise TelegramError(f"getUpdates: {body.get('description', 'unknown error')}")
        updates = body.get("result") or []
        if updates:
            self._offset = max(int(u["update_id"]) for u in updates) + 1
        return list(updates)

    def download_file(self, file_id: str) -> bytes:
        """Fetch a file by Telegram file_id (voice notes arrive this way)."""
        meta = self.call("getFile", {"file_id": file_id})
        path = meta.get("file_path")
        if not path:
            raise TelegramError("getFile returned no file_path")
        url = f"{self.base_url}/file/bot{self.token}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise TelegramError(f"download failed: {type(exc).__name__}") from exc

    # -------------------------------------------------------------- helpers
    def is_owner(self, chat_id: Any) -> bool:
        try:
            return int(chat_id) == self.owner_chat_id
        except (TypeError, ValueError):
            return False

    def poll_forever(self, stop_after: int | None = None, pause: float = 0.0) -> Iterator[dict]:
        """Yield updates forever (or for ``stop_after`` updates), tolerating drops."""
        seen = 0
        while stop_after is None or seen < stop_after:
            try:
                updates = self.get_updates()
            except TelegramError:
                time.sleep(5)
                continue
            for update in updates:
                seen += 1
                yield update
                if stop_after is not None and seen >= stop_after:
                    return
            if pause:
                time.sleep(pause)


def _parse(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception as exc:
        raise TelegramError("bad JSON from Telegram") from exc
    if not isinstance(data, dict):
        raise TelegramError("unexpected response shape")
    return data