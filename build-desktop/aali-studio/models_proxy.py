"""Aali Studio — model registry, API adapters and the key store.

Everything here is **API only**: Studio never downloads a model to the PC.
The default model, HWK-AZiZA, is not an API at all — it is the owner's own
brain served locally on :5055, which studio_server proxies.

Keys live OUTSIDE the repo in ``%APPDATA%/AaliStudio/keys.json`` (override the
directory with ``AALI_STUDIO_CONFIG_DIR`` for tests). A key can also come from
the environment as ``AALI_STUDIO_KEY_<ID>`` which wins over the file, so a
machine-wide secret never has to be written to disk twice.

Only stdlib + Flask-free code lives here so the module can be unit-tested
without the server.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

_HERE = Path(__file__).resolve().parent
for _extra in (_HERE.parents[1] / "file-agent", _HERE):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

try:  # the platform-correct config dir (macOS Application Support, XDG, …)
    from file_agent import hwk_paths
except ImportError:  # frozen exe without the brain package on the path
    hwk_paths = None  # type: ignore[assignment]

# ————— configuration —————

def cfg_dir() -> str:
    """Where Studio keeps its settings + keys — resolved FRESH every call.

    Module constants here would freeze the path at import time, which makes
    the path impossible to redirect (tests, and an owner who moves AppData).
    The directory is the PLATFORM's own convention via hwk_paths (macOS:
    ~/Library/Application Support/AaliStudio), not a hardcoded %APPDATA%.
    """
    override = os.getenv("AALI_STUDIO_CONFIG_DIR")
    if override:
        return override
    if hwk_paths is not None:
        return str(hwk_paths.config_dir("AaliStudio"))
    return os.path.join(os.path.expanduser("~"), ".AaliStudio")


def keys_file() -> str:
    return os.path.join(cfg_dir(), "keys.json")


def settings_file() -> str:
    return os.path.join(cfg_dir(), "settings.json")

DEFAULT_BRAIN_URL = "http://127.0.0.1:5055"
#: The brain on :5055 can run in KEY MODE, where every gated route (ask,
#: ask/stream) answers a polite 404 without X-API-Key — /api/health is the
#: only exempt one. Studio therefore carries a brain key too, stored in the
#: same key store under this field.
BRAIN_KEY_FIELD = "brain"
#: The label the owner chose for his own brain. Tribute to his grandmother,
#: Azeza (عزيزة) — see the About box in the Studio UI.
DEFAULT_MODEL_ID = "hwk-aziza"

#: Cosmetic pacing for replaying the local brain's one-shot answer word by
#: word in the agent panel. 0 disables the effect (everything at once).
REPLAY_MS = int(os.getenv("AALI_STUDIO_REPLAY_MS", "10") or 0)

MAX_KEY_CHARS = 400


class StudioError(Exception):
    """Expected, user-facing error (rendered in Arabic by the client)."""


# ————— the registry —————

def _openai_compat(model_id: str, label: str, base_url: str, model: str,
                   key_field: str, note: str = "",
                   headers: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": model_id, "label": label, "kind": "openai",
        "base_url": base_url, "model": model, "key_field": key_field,
        "note": note, "headers": headers or {},
    }


MODELS: list[dict[str, Any]] = [
    {
        "id": DEFAULT_MODEL_ID,
        "label": "HWK-AZiZA (محلي)",
        "kind": "local",
        "note": "عقل آلي المبني من الصفر على هذا الحاسب — بدون إنترنت وبدون مفتاح",
        "default": True,
    },
    _openai_compat("glm-4-flash", "GLM-4-Flash", "https://open.bigmodel.cn/api/paas/v4",
                   "glm-4-flash", "zhipu",
                   "zhipu.cn — مفتاح من platform.bigmodel.cn"),
    _openai_compat("glm-4-plus", "GLM-4-Plus", "https://open.bigmodel.cn/api/paas/v4",
                   "glm-4-plus", "zhipu",
                   "zhipu.cn — نفس المفتاح، نموذج أذكى وأغلى"),
    _openai_compat("deepseek-v3", "DeepSeek-V3", "https://api.deepseek.com",
                   "deepseek-chat", "deepseek", "deepseek.com — رخيص وسريع"),
    _openai_compat("deepseek-r1", "DeepSeek-R1", "https://api.deepseek.com",
                   "deepseek-reasoner", "deepseek",
                   "يستدل خطوة بخطوة — يظهر تفكيره في لوحة الوكيل"),
    _openai_compat("groq-llama-3.3", "Groq · Llama 3.3",
                   "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile",
                   "groq", "أسرع نموذج مجاني تقريباً"),
    _openai_compat("openrouter-free", "OpenRouter (مجاني)",
                   "https://openrouter.ai/api/v1",
                   "meta-llama/llama-3.3-70b-instruct:free", "openrouter",
                   "نماذج مجانية — حدود استخدام سخية",
                   headers={"HTTP-Referer": "https://aali.local",
                            "X-Title": "Aali Studio"}),
    _openai_compat("together-free", "Together AI (مجاني)",
                   "https://api.together.xyz/v1",
                   "meta-llama/Llama-3.3-70B-Instruct-Turbo", "together",
                   "طبقة مجانية سخية"),
    {
        "id": "custom",
        "label": "نقطة وصول مخصصة (Custom)",
        "kind": "openai",
        "base_url": "", "model": "", "key_field": "custom",
        "note": "أي خادم متوافق مع OpenAI: ضع العنوان واسم النموذج في الإعدادات",
    },
]

_BY_ID = {m["id"]: m for m in MODELS}


def model_def(model_id: str) -> dict[str, Any]:
    """Resolve a model id, refusing unknown ones (never a silent default)."""
    spec = _BY_ID.get(str(model_id or "").strip())
    if spec is None:
        raise StudioError(f"نموذج غير معروف: {model_id}")
    return spec


def default_model() -> str:
    return DEFAULT_MODEL_ID


# ————— settings + keys —————

def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    # Best effort lock-down: on Windows os.replace keeps the temp file's
    # (default) ACL, so the final file is user-only by inheritance.
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows without POSIX bits — the AppData ACL already applies.


def settings() -> dict[str, Any]:
    """Non-secret Studio settings (last model, brain URL, custom endpoint,
    and the UI preferences the owner set in the keys dialog)."""
    data = _load_json(settings_file())
    data.setdefault("model", DEFAULT_MODEL_ID)
    data.setdefault("brain_url", os.getenv("AALI_BRAIN_URL", DEFAULT_BRAIN_URL))
    data.setdefault("workspace", "")
    data.setdefault("custom_base_url", "")
    data.setdefault("custom_model", "")
    # UI/UX preferences (2026-10-03): the owner asked for a comfortable IDE,
    # not a dense one. Server-side defaults so a fresh install is readable.
    data.setdefault("font_size", "14")
    data.setdefault("word_wrap", "on")
    data.setdefault("terminal_visible", "1")
    data.setdefault("show_thinking", "1")
    return data


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge `patch` into the settings file. Unknown keys are refused."""
    allowed = {"model", "brain_url", "workspace", "custom_base_url",
               "custom_model",
               # UI preferences — still strings, still length-capped below.
               "font_size", "word_wrap", "terminal_visible", "show_thinking"}
    clean: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        if key not in allowed:
            raise StudioError(f"إعداد غير معروف: {key}")
        text = str(value if value is not None else "").strip()
        if len(text) > 400:
            raise StudioError("قيمة الإعداد طويلة جداً")
        clean[key] = text
    current = _load_json(settings_file())
    current.update(clean)
    if "model" in clean and clean["model"] not in _BY_ID:
        raise StudioError(f"نموذج غير معروف: {clean['model']}")
    if "brain_url" in clean and clean["brain_url"]:
        # The brain key travels in an X-API-Key header to this address, so the
        # scheme is a security boundary, not a formatting nit: file:// and
        # javascript: have no business receiving a credential.
        from urllib.parse import urlparse
        parsed = urlparse(clean["brain_url"])
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise StudioError(
                "عنوان العقل يجب أن يبدأ بـ http:// أو https:// "
                "(مثال: http://192.168.1.13:5055)")
    _save_json(settings_file(), current)
    return settings()


def keys() -> dict[str, str]:
    stored = _load_json(keys_file())
    return {str(k): str(v) for k, v in stored.items() if str(v).strip()}


def get_key(field: str) -> str:
    """Env override wins over the file; empty string means "not set"."""
    env = os.getenv(f"AALI_STUDIO_KEY_{str(field).upper()}")
    if env and env.strip():
        return env.strip()
    return keys().get(str(field), "").strip()


def save_key(field: str, value: str) -> None:
    field, value = str(field or "").strip(), str(value or "").strip()
    if not field:
        raise StudioError("اسم المفتاح مطلوب")
    if len(value) > MAX_KEY_CHARS:
        raise StudioError("المفتاح طويل جداً")
    stored = _load_json(keys_file())
    if value:
        stored[field] = value
    else:
        stored.pop(field, None)  # empty saves DELETE — no stale secrets
    _save_json(keys_file(), stored)


def key_status() -> dict[str, Any]:
    """Which models can actually run — NEVER the key values themselves."""
    models: list[dict[str, Any]] = []
    for spec in MODELS:
        entry = {"id": spec["id"], "label": spec["label"],
                 "kind": spec["kind"], "note": spec.get("note", "")}
        if spec["kind"] == "local":
            entry["ready"] = True
        else:
            field = str(spec.get("key_field", ""))
            secret = get_key(field)
            entry["key_field"] = field
            entry["ready"] = bool(secret)
            if secret:
                # Only a hint: first 3 + last 2 characters. Enough to tell
                # two keys apart in the settings UI, useless to a reader.
                entry["hint"] = f"{secret[:3]}…{secret[-2:]}"
        models.append(entry)
    custom = settings()
    if custom.get("custom_base_url") and custom.get("custom_model"):
        models[-1]["ready"] = bool(get_key("custom"))
    brain_secret = get_key(BRAIN_KEY_FIELD)
    brain = {"field": BRAIN_KEY_FIELD, "ready": bool(brain_secret)}
    if brain_secret:
        brain["hint"] = f"{brain_secret[:3]}…{brain_secret[-2:]}"
    return {"models": models, "brain_key": brain, "config_dir": cfg_dir()}


def models_payload() -> dict[str, Any]:
    status = key_status()
    current = settings()
    ready = {m["id"]: m["ready"] for m in status["models"]}
    return {
        "default": DEFAULT_MODEL_ID,
        "current": current.get("model", DEFAULT_MODEL_ID),
        "brain_url": current.get("brain_url", DEFAULT_BRAIN_URL),
        "custom": {"base_url": current.get("custom_base_url", ""),
                   "model": current.get("custom_model", "")},
        "models": [
            {"id": m["id"], "label": m["label"], "kind": m["kind"],
             "note": m["note"], "ready": ready.get(m["id"], False)}
            for m in MODELS
        ],
    }


# ————— OpenAI-compatible streaming adapter —————

def _chat_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise StudioError("عنوان نقطة الوصول فارغ")
    if not base.startswith(("http://", "https://")):
        raise StudioError("عنوان نقطة الوصول يجب أن يبدأ بـ http أو https")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _resolve_openai_spec(spec: dict[str, Any]) -> tuple[str, str, str, str]:
    """(chat_url, model_name, api_key, key_field) for an openai-kind model."""
    conf = settings()
    if spec["id"] == "custom":
        return (_chat_url(conf.get("custom_base_url", "")),
                conf.get("custom_model", ""), get_key("custom"), "custom")
    key = get_key(str(spec.get("key_field", "")))
    if not key:
        raise StudioError(
            f"لا يوجد مفتاح لـ {spec['label']} — أضفه من إعدادات Studio")
    return (_chat_url(str(spec.get("base_url", ""))),
            str(spec.get("model", "")), key,
            str(spec.get("key_field", "")))


def _sse_payloads(raw: str) -> Iterator[dict[str, Any]]:
    """Parse an SSE body chunk-by-chunk into JSON payloads (never raises)."""
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                parsed = json.loads(data)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                yield parsed


def stream_openai(spec: dict[str, Any], messages: list[dict[str, str]],
                  stop_check=lambda: False) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(kind, text, extra)`` where kind is thinking | text | error.

    `thinking` comes from DeepSeek-R1's ``reasoning_content`` deltas, which is
    REAL chain-of-thought from the provider — not invented text.
    """
    url, model, key, _field = _resolve_openai_spec(spec)
    if not model:
        raise StudioError("اسم النموذج غير محدد")
    body = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.4,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}",
               "Accept": "text/event-stream"}
    headers.update({str(k): str(v) for k, v in (spec.get("headers") or {}).items()})
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=180)
    except urllib.error.HTTPError as exc:  # provider said no — say why
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - the status alone must still surface
            pass
        raise StudioError(f"الخادم رد {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise StudioError(f"تعذر الوصول إلى المزوّد: {exc.reason}") from exc

    with response:
        while True:
            if stop_check():
                return
            chunk = response.read(1)
            if not chunk:
                break
            # read(1) is slow — pull a whole line's worth after it.
            rest = response.readline()
            line = (chunk + rest).decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                return
            try:
                parsed = json.loads(data)
            except ValueError:
                continue
            for choice in (parsed.get("choices") or []):
                delta = choice.get("delta") or {}
                reasoning = str(delta.get("reasoning_content") or "")
                if reasoning:
                    yield "thinking", reasoning, None
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield "text", content, None
                if choice.get("finish_reason") == "length":
                    yield "text", "\n\n[بلغ الحد الأقصى لطول الرد]\n\n", None


def chunks_for_replay(text: str) -> list[str]:
    """Split a finished reply into small pieces for the panel's reveal effect.

    Words stay whole (Arabic included) so nothing is ever cut mid-word; the
    CONTENT is verbatim — only the pacing is cosmetic.
    """
    import re
    return [piece for piece in re.findall(r"\s+|\S+", text) if piece]
