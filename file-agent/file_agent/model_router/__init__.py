"""Pluggable model router — 12 built-in providers with style, key, and model mapping.

Lightweight stdlib dispatch path:
- Every OpenAI-compatible provider carries a ``base_url`` (and, optionally,
  a ``path``) so the router can build the request with ``urllib`` alone —
  no third-party HTTP dependency.
- ``dispatch()`` sends a system + user messages payload to the provider's
  chat/completions endpoint and returns the first choice's content.
- When the caller passes ``api_key``, it is used directly; otherwise the
  provider's configured env var is read.
"""

from __future__ import annotations

import os
from typing import Any

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "style": "openai",
        "env_key": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "openrouter": {
        "style": "openai",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-3.5-sonnet",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "anthropic": {
        "style": "openai",
        "env_key": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-sonnet-latest",
        "base_url": "https://api.anthropic.com/v1",
    },
    "gemini": {
        "style": "openai",
        "env_key": "GEMINI_API_KEY",
        "default_model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "ollama": {
        "style": "openai",
        "env_key": "OLLAMA_API_KEY",
        "default_model": "llama3.2",
        "base_url": "http://localhost:11434/v1",
    },
    "groq": {
        "style": "openai",
        "env_key": "GROQ_API_KEY",
        "default_model": "llama-3.1-8b-instant",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "together": {
        "style": "openai",
        "env_key": "TOGETHER_API_KEY",
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "base_url": "https://api.together.ai/v1",
    },
    "mistral": {
        "style": "openai",
        "env_key": "MISTRAL_API_KEY",
        "default_model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
    },
    "deepseek": {
        "style": "openai",
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    },
    "perplexity": {
        "style": "openai",
        "env_key": "PERPLEXITY_API_KEY",
        "default_model": "llama-3.1-sonar-small-128k-online",
        "base_url": "https://api.perplexity.ai",
    },
    "ai21": {
        "style": "openai",
        "env_key": "AI21_API_KEY",
        "default_model": "j2-ultra",
        "base_url": "https://api.ai21.com/v1",
    },
    "cohere": {
        "style": "openai",
        "env_key": "COHERE_API_KEY",
        "default_model": "command-r-plus",
        "base_url": "https://api.cohere.com/v1",
    },
}

_KNOWN_URLS: dict[str, str | None] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": None,
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "ollama": "http://localhost:11434/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "perplexity": "https://api.perplexity.ai",
    "ai21": "https://api.ai21.com/v1",
    "cohere": "https://api.cohere.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def provider_style(provider: str) -> str:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise ProviderError(f"unknown provider: {provider}")
    return cfg["style"]


def api_key_for(provider: str) -> str | None:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return None
    return cfg["env_key"]


def api_key_value(provider: str) -> str | None:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return None
    return os.getenv(cfg["env_key"])


def default_model_for(provider: str) -> str:
    cfg = PROVIDERS.get(provider)
    return cfg["default_model"] if cfg else ""


def register_provider(name: str, style: str, env_key: str, default_model: str) -> None:
    PROVIDERS[name] = {
        "style": style,
        "env_key": env_key,
        "default_model": default_model,
    }


def _build_chat_payload(provider: str, user_message: str, system_prompt: str) -> dict:
    cfg = PROVIDERS[provider]
    model = cfg["default_model"]
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 2000,
        "temperature": 0.7,
    }
    return payload


def dispatch(user_message: str, system_prompt: str, provider: str, api_key: str | None = None) -> str:
    if provider not in PROVIDERS:
        raise ProviderError(f"unknown provider: {provider}")
    key = api_key_for(provider)
    if key is None:
        raise ProviderError(f"unknown provider: {provider}")
    if api_key is not None:
        key_value = api_key
    else:
        key_value = api_key_value(provider)
        if key_value is None:
            raise ProviderError(f"no API key configured for {provider}")
    url = _chat_completion_url(provider)
    if url is None:
        raise ProviderError(f"no chat endpoint for {provider}")
    payload = _build_chat_payload(provider, user_message, system_prompt)
    headers = {
        "Authorization": f"Bearer {key_value}",
        "Content-Type": "application/json",
    }
    import json as _json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    try:
        body = _json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=30) as resp:
            data = _json.load(resp)
    except HTTPError as exc:
        text = (exc.read() or b"").decode("utf-8", "replace")[:300]
        raise ProviderError(f"{provider} returned {exc.code}: {text}")
    except URLError as exc:
        raise ProviderError(f"{provider} unreachable: {exc.reason}")
    except Exception as exc:
        raise ProviderError(f"{provider} error: {exc}")
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"{provider} returned no choices: {data}")
    msg = (choices[0].get("message") or {})
    content = msg.get("content") or ""
    return content


def _chat_completion_url(provider: str) -> str | None:
    cfg = PROVIDERS.get(provider, {})
    base = cfg.get("base_url")
    if base:
        path = cfg.get("path") or "/chat/completions"
        return base.rstrip("/") + path
    return _KNOWN_URLS.get(provider)


class ProviderError(RuntimeError):
    """Expected, user-facing connector error."""
    pass
