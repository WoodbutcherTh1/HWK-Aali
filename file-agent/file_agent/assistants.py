"""Custom assistants (Wave 4, 2026-09-25).

Named persona configs OVER the existing machinery: an assistant is a
small record (name, icon, tagline, instruction, optional project pin)
that a user binds to their session. At ask time the persona instruction
is injected as labeled text — the SAME mechanism as project
instructions; nothing new executes and no policy gate is weakened.

SECURITY CONTRACT (documented + test-pinned):
- An assistant can NEVER change the tool policy. The client-supplied
  policy and the server-enforced guest policy keep their precedence;
  the persona is TEXT only.
- ns-scoped like projects/prompts: users see and bind only their own;
  one user's assistant can never leak into another's context.
- Guests have no assistants surface at all (uniform 404 in app.py).

Store: D:/hwk-data/assistants.jsonl (small config records; same JSONL
pattern as prompts/shares).
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

DEFAULT_STORE = Path("D:/hwk-data/assistants.jsonl")

MAX_NAME = 40
MAX_TAGLINE = 120
MAX_INSTRUCTION = 2000
MAX_PER_NS = 50

_ICONS = ("🎭", "🤖", "🧑‍🏫", "🧑‍💻", "📐", "🧾", "🌐", "🎮", "📚", "✦")


def _load(path: Path | str | None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_STORE
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                out.append(rec)
    except OSError:
        return []
    return out


def _save(rows: list[dict[str, Any]], path: Path | str | None) -> None:
    p = Path(path) if path else DEFAULT_STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(p)


def _clean(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"], "name": rec.get("name", ""),
        "icon": rec.get("icon", "🎭"),
        "tagline": rec.get("tagline", ""),
        "instruction": rec.get("instruction", ""),
        "project_id": rec.get("project_id") or None,
        "ns": rec.get("ns", ""),
        "created": rec.get("created", 0),
    }


def create_assistant(name: str, ns: str = "", *, icon: str = "🎭",
                     tagline: str = "", instruction: str = "",
                     project_id: str | None = None,
                     path: Path | str | None = None) -> dict[str, Any]:
    name = (name or "").strip()[:MAX_NAME]
    if not name:
        return {"ok": False, "error": "اسم المساعد مطلوب"}
    rows = _load(path)
    if sum(1 for r in rows if r.get("ns") == str(ns or "")) >= MAX_PER_NS:
        return {"ok": False, "error": f"الحد {MAX_PER_NS} مساعداً مخصصاً"}
    rec = {
        "id": "as_" + secrets.token_hex(6),
        "ns": str(ns or ""),
        "name": name,
        "icon": (icon or "🎭")[:8],
        "tagline": (tagline or "").strip()[:MAX_TAGLINE],
        "instruction": (instruction or "").strip()[:MAX_INSTRUCTION],
        "project_id": (str(project_id) if project_id else None),
        "created": time.time(),
    }
    rows.append(rec)
    _save(rows, path)
    return {"ok": True, "assistant": _clean(rec)}


def get_assistant(aid: str, ns: str | None = "",
                  path: Path | str | None = None) -> dict[str, Any] | None:
    for rec in _load(path):
        if rec.get("id") == aid and (ns is None or
                                     rec.get("ns") == str(ns or "")):
            return _clean(rec)
    return None


def list_assistants(ns: str = "",
                    path: Path | str | None = None) -> list[dict[str, Any]]:
    mine = [_clean(r) for r in _load(path) if r.get("ns") == str(ns or "")]
    mine.sort(key=lambda r: r.get("created", 0), reverse=True)
    return mine


def update_assistant(aid: str, ns: str = "", *, name: str | None = None,
                     icon: str | None = None, tagline: str | None = None,
                     instruction: str | None = None,
                     project_id: str | None | bool = None,
                     path: Path | str | None = None) -> dict[str, Any]:
    """Partial update. project_id: str = pin; None = leave; False = unpin."""
    rows = _load(path)
    rec = next((r for r in rows
                if r.get("id") == aid and r.get("ns") == str(ns or "")), None)
    if rec is None:
        return {"ok": False, "error": "المساعد غير موجود"}
    if name is not None:
        name = name.strip()[:MAX_NAME]
        if not name:
            return {"ok": False, "error": "الاسم لا يمكن أن يكون فارغاً"}
        rec["name"] = name
    if icon is not None:
        rec["icon"] = (icon or "🎭")[:8]
    if tagline is not None:
        rec["tagline"] = tagline.strip()[:MAX_TAGLINE]
    if instruction is not None:
        rec["instruction"] = instruction.strip()[:MAX_INSTRUCTION]
    if project_id is not False and project_id is not None:
        rec["project_id"] = str(project_id)
    elif project_id is False:
        rec["project_id"] = None
    _save(rows, path)
    return {"ok": True, "assistant": _clean(rec)}


def delete_assistant(aid: str, ns: str = "",
                     path: Path | str | None = None) -> dict[str, Any]:
    rows = _load(path)
    kept = [r for r in rows
            if not (r.get("id") == aid and r.get("ns") == str(ns or ""))]
    if len(kept) == len(rows):
        return {"ok": False, "error": "المساعد غير موجود"}
    _save(kept, path)
    return {"ok": True}


def persona_block(assistant: dict[str, Any] | None) -> str:
    """The labeled text injected at ask time. Empty when no assistant —
    honest empty, never a fake persona."""
    if not assistant:
        return ""
    parts = [f"أنت الآن تتصرف كمساعد مخصص باسم «{assistant.get('name', '')}»"]
    if assistant.get("tagline"):
        parts.append(f"وصفه: {assistant['tagline']}")
    if assistant.get("instruction"):
        parts.append(f"تعليمات الشخصية: {assistant['instruction']}")
    return "[" + " — ".join(parts) + "]"
