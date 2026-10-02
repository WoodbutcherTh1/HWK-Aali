# -*- coding: utf-8 -*-
"""Voice agent plugin interface (Phase 1: INTERFACE ONLY — no plugin yet).

A plugin can observe or transform any pipeline stage without touching core
code. Implement one or more hooks and register with
``voice.core.plugins.register`` (wiring lands with the first real plugin).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class VoicePlugin(Protocol):
    """Lifecycle hooks — implement any subset; missing hooks are skipped."""

    name: str

    def on_transcript(self, text: str, lang: str, meta: Dict[str, Any]) -> Optional[str]:
        """Observe/rewrite the user transcript. Return new text or None."""
        ...

    def on_reply(self, reply: str, meta: Dict[str, Any]) -> Optional[str]:
        """Observe/rewrite Aali's text reply. Return new text or None."""
        ...

    def on_chunks(self, chunks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """Observe/reorder/annotate TTS chunks. Return new list or None."""
        ...

    def on_turn_done(self, result: Dict[str, Any]) -> None:
        """Observe the finished turn result (fire-and-forget)."""
        ...


_REGISTRY: list = []


def register(plugin: Any) -> None:
    """Register a plugin instance (duplicates by name are ignored)."""
    name = getattr(plugin, "name", None)
    if not name:
        raise ValueError("plugin must have a `name` attribute")
    if any(getattr(p, "name", None) == name for p in _REGISTRY):
        return
    _REGISTRY.append(plugin)


def registered() -> list:
    return list(_REGISTRY)


def clear() -> None:
    _REGISTRY.clear()


def apply_transcript(text: str, lang: str, meta: Dict[str, Any]) -> str:
    for p in _REGISTRY:
        hook = getattr(p, "on_transcript", None)
        if hook is None:
            continue
        out = hook(text, lang, meta)
        if out:
            text = out
    return text


def apply_reply(reply: str, meta: Dict[str, Any]) -> str:
    for p in _REGISTRY:
        hook = getattr(p, "on_reply", None)
        if hook is None:
            continue
        out = hook(reply, meta)
        if out:
            reply = out
    return reply


def apply_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for p in _REGISTRY:
        hook = getattr(p, "on_chunks", None)
        if hook is None:
            continue
        out = hook(chunks)
        if out:
            chunks = out
    return chunks
