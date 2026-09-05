"""Pluggable remote model "connectors": Anthropic (Claude), OpenAI (GPT), and
Google (Gemini), plus the existing OpenRouter/managed-key path.

"Connecting a plugin/connector" here means: pick a provider and set its API
key as an environment variable before starting the app (OPENAI_API_KEY,
ANTHROPIC_API_KEY, GEMINI_API_KEY, or OPENROUTER_API_KEY) — Aali never asks
for a key inside the chat itself. cloud mode then uses that provider with
the exact same tool loop (file tools, skills, web tools) as every other mode.

OpenRouter and OpenAI already share one schema (OpenAI's chat/completions),
so both go through the existing _agent_loop cloud branch in agent_loop.py
unchanged. Anthropic's Messages API and Gemini's generateContent API have
different request/response shapes, so they get their own small loops here —
each one still calls the same execute_tool/_policy_gate the other loops use,
so file-safety and the auto/aggressive/always_ask policy apply identically.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

PROVIDERS: dict[str, dict[str, str]] = {
    "openrouter": {"style": "openai", "env_key": "OPENROUTER_API_KEY", "default_model": "anthropic/claude-3.5-sonnet"},
    "openai": {"style": "openai", "env_key": "OPENAI_API_KEY", "default_model": "gpt-4o-mini"},
    "anthropic": {"style": "anthropic", "env_key": "ANTHROPIC_API_KEY", "default_model": "claude-3-5-sonnet-latest"},
    "gemini": {"style": "gemini", "env_key": "GEMINI_API_KEY", "default_model": "gemini-2.0-flash"},
}


class ProviderError(RuntimeError):
    """Expected, user-facing connector error."""


def provider_style(provider: str) -> str:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise ProviderError(f"unknown provider: {provider}")
    return cfg["style"]


def api_key_for(provider: str) -> str | None:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return None
    return os.getenv(cfg["env_key"])


def default_model_for(provider: str) -> str:
    cfg = PROVIDERS.get(provider)
    return cfg["default_model"] if cfg else ""


# --- Anthropic --------------------------------------------------------------

def _tools_to_anthropic(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for entry in tool_defs:
        fn = entry.get("function", {})
        out.append({
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


def anthropic_loop(
    user_message: str,
    system_prompt: str,
    api_key: str,
    model: str,
    tool_defs: list[dict[str, Any]],
    run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_iterations: int,
    history: list[dict[str, str]] | None = None,
) -> str:
    messages: list[dict[str, Any]] = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message.strip()})

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    for _ in range(max_iterations):
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": messages,
            "tools": _tools_to_anthropic(tool_defs),
        }
        try:
            response = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=90)
        except requests.RequestException as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"Anthropic returned HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        blocks = data.get("content", [])
        messages.append({"role": "assistant", "content": blocks})

        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            if not text:
                raise ProviderError("Anthropic returned an empty final response")
            return text

        result_blocks = []
        for call in tool_uses:
            result = run_tool(call.get("name", ""), call.get("input") or {})
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": call.get("id"),
                "content": json.dumps(result, ensure_ascii=False)[:6000],
            })
        messages.append({"role": "user", "content": result_blocks})

    raise ProviderError(f"Anthropic connector reached its maximum of {max_iterations} iterations")


# --- Gemini ------------------------------------------------------------------

def _tools_to_gemini(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    declarations = []
    for entry in tool_defs:
        fn = entry.get("function", {})
        declarations.append({
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return [{"functionDeclarations": declarations}]


def gemini_loop(
    user_message: str,
    system_prompt: str,
    api_key: str,
    model: str,
    tool_defs: list[dict[str, Any]],
    run_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_iterations: int,
    history: list[dict[str, str]] | None = None,
) -> str:
    contents: list[dict[str, Any]] = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
    contents.append({"role": "user", "parts": [{"text": user_message.strip()}]})

    url = GEMINI_URL_TEMPLATE.format(model=model)
    for _ in range(max_iterations):
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "tools": _tools_to_gemini(tool_defs),
        }
        try:
            response = requests.post(
                url, params={"key": api_key}, json=payload, timeout=90,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"Gemini returned HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini returned no candidates (likely blocked by safety filters)")
        parts = candidates[0].get("content", {}).get("parts", [])
        contents.append({"role": "model", "parts": parts})

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not function_calls:
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise ProviderError("Gemini returned an empty final response")
            return text

        response_parts = []
        for call in function_calls:
            result = run_tool(call.get("name", ""), call.get("args") or {})
            response_parts.append({
                "functionResponse": {
                    "name": call.get("name", ""),
                    "response": {"result": result},
                }
            })
        contents.append({"role": "user", "parts": response_parts})

    raise ProviderError(f"Gemini connector reached its maximum of {max_iterations} iterations")
