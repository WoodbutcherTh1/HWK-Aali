"""Aali's long-term memory: what the user said, when, and in what order.

Design goals (owner request, 2026-09-06):
- The user tells Aali something at 07:00 ("do NOT push to GitHub"); Aali must
  still know it at 19:00, after restarts, in a NEW conversation.
- When the user later changes their mind on the same topic, Aali must follow
  the NEWEST instruction AND be able to say "earlier you told me the opposite"
  instead of silently pretending there was never a conflict.
- Only user statements are stored verbatim - never Aali's own beliefs - so the
  memory cannot drift into self-reinvented facts (anti-hallucination rule).
- Everything is timestamped so SFT data can be generated from real behaviour.

Storage: one JSON file (default ``~/.aali/aali_memory.json``) plus an append-only
conversation log (``~/.aali/aali_conversations.jsonl``) that captures raw turns
for later mentor-SFT conversion. Both live OUTSIDE any repo so they survive
checkouts, and both can be redirected with the AALI_MEMORY_DIR env var
(the unit tests use this).
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid

import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MEMORY_DIR_ENV = "AALI_MEMORY_DIR"
MEMORY_FILE_NAME = "aali_memory.json"
CONVERSATION_LOG_NAME = "aali_conversations.jsonl"

KINDS = ("decision", "preference", "fact", "instruction")
MAX_ENTRIES = 200          # prune target for the memory store
MAX_TEXT_CHARS = 2000      # stored per entry
MAX_LOG_CHARS = 4000       # stored per conversation turn
MAX_RENDER_ENTRIES = 25

_LOCK = threading.Lock()

# User phrases that almost always carry a durable instruction. Only these are
# auto-captured by record_turn; everything else waits for an explicit
# memory(save) call from the model.
_AUTO_CAPTURE_RE = re.compile(
    r"(من الآن|لا تحاول|لا تحذف|لا تنسَ|لا تنسى|تذكّر|تذكر أن|احفظ|احفظ هذا|"
    r"always\s|never\s|don't\s|do not\s|remember\s+(that|this)|from now on)",
    re.IGNORECASE,
)


class MemoryError(ValueError):
    """Caller misuse (bad kind, refusing to store agent beliefs, etc.)."""


# --- secret redaction (lesson: real industry leak incidents) ---------
# Memory entries are injected into system prompts and can therefore be SENT
# TO CLOUD PROVIDERS. A key, token, or password pasted by the user must never
# ride along. We keep the sentence but replace the secret with a redaction
# marker so the fact is still remembered without the secret existing anywhere
# outside the user's machine.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}\b"),                # openai-style keys
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}\b"),                  # bearer tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),                        # github tokens
    re.compile(r"(?i)\bAKIA[0-9A-Z]{12,}\b"),                             # aws access keys
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9\-]{8,}\b"),                  # slack tokens
    re.compile(r"(?i)(api[_\-]?key|api[_\-]?secret|password|passwd|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(مفتاح|كلمة\s+السر|الباسورد)\s*[:=]?\s*\S+"),          # Arabic secret mentions
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),                    # private keys
)
_SECRET_MARKER = "[REDACTED-SECRET]"


def redact_secrets(text: str) -> str:
    """Replace credential-looking substrings with a redaction marker."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_SECRET_MARKER, text)
    return text


# ---------------------------------------------------------------------------
# paths / storage
# ---------------------------------------------------------------------------

def memory_dir(dir_override: str | Path | None = None) -> Path:
    """Resolve the memory directory (env var wins over the default home path)."""
    if dir_override is not None:
        path = Path(dir_override)
    else:
        env = os.getenv(MEMORY_DIR_ENV, "").strip()
        path = Path(env) if env else Path.home() / ".aali"
    return path


def memory_path(dir_override: str | Path | None = None) -> Path:
    return memory_dir(dir_override) / MEMORY_FILE_NAME


def conversation_log_path(dir_override: str | Path | None = None) -> Path:
    return memory_dir(dir_override) / CONVERSATION_LOG_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text: str) -> str:
    """Case/whitespace-insensitive form used for dedup matching."""
    return re.sub(r"\s+", " ", text.strip().casefold())


def _new_entry(text: str, kind: str, topic: str | None, source: str,
               auto: bool, now: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "kind": kind,
        "text": text,
        "topic": (topic or "").strip() or None,
        "source": source,
        "auto": auto,
        "created": now,
        "updated": now,
        "confirmations": 1,
        "superseded_by": None,
    }


def _load(dir_override: str | Path | None = None) -> dict[str, Any]:
    """Load the store; a corrupt/missing file yields a fresh store (never raises)."""
    path = memory_path(dir_override)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "entries": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return {"version": 1, "entries": []}
    payload.setdefault("version", 1)
    return payload


def _save(store: dict[str, Any], dir_override: str | Path | None = None) -> None:
    """Atomic write: tmp file + os.replace, under the process-wide lock."""
    path = memory_path(dir_override)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    os.replace(tmp, path)


def _prune(store: dict[str, Any]) -> None:
    """Keep the store bounded: drop oldest auto-captured entries first."""
    entries = store["entries"]
    if len(entries) <= MAX_ENTRIES:
        return
    autos = [e for e in entries if e.get("auto")]
    drop = len(entries) - MAX_ENTRIES
    for entry in autos[:drop]:
        entry["superseded_by"] = "pruned"
    kept = [e for e in entries if not e.get("superseded_by") == "pruned"]
    if len(kept) > MAX_ENTRIES:  # no auto entries to drop - oldest lose
        kept = sorted(kept, key=lambda e: e.get("created", ""))[drop:]
    store["entries"] = kept


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def remember(
    text: str,
    kind: str = "fact",
    topic: str | None = None,
    source: str = "user",
    auto: bool = False,
    dir_override: str | Path | None = None,
) -> dict[str, Any]:
    """Store one user statement. Returns the entry plus any older entry on the
    same topic it contradicts (so the caller can surface the conflict)."""
    text = str(text).strip()
    if not text:
        raise MemoryError("لا يمكن حفظ ذاكرة فارغة - text is empty")
    if kind not in KINDS:
        raise MemoryError(f"kind must be one of {KINDS}, got {kind!r}")
    if source != "user":
        # Anti-hallucination guard: Aali's own beliefs/claims are never saved
        # as if they came from the user.
        raise MemoryError(
            "only user statements are stored (source='user'); "
            "Aali's own beliefs would make the memory hallucinate"
        )
    # Security guard (lesson: real industry leaks): a secret
    # pasted in chat must not end up in memory blocks that get sent to cloud
    # providers. Keep the statement, drop the secret.
    redacted = redact_secrets(text)
    text = redacted if redacted != text else text
    text = text[:MAX_TEXT_CHARS]
    now = _now_iso()
    with _LOCK:
        store = _load(dir_override)
        entries = store["entries"]
        normalized = _normalize(text)

        # Re-assertion of the same statement: bump confidence, keep history.
        for entry in entries:
            if entry.get("kind") == kind and _normalize(str(entry.get("text", ""))) == normalized:
                entry["confirmations"] = int(entry.get("confirmations", 1)) + 1
                entry["updated"] = now
                return {"ok": True, "result": {"action": "reaffirmed",
                                               "entry": entry, "contradiction": None}}

        # Contradiction: same kind + same topic, different text.
        contradiction = None
        wanted_topic = (topic or "").strip() or None
        same_scope = [
            e for e in entries
            if e.get("kind") == kind
            and (e.get("topic") or None) == wanted_topic
            and _normalize(str(e.get("text", ""))) != normalized
        ]
        if same_scope:
            newest_other = max(same_scope, key=lambda e: e.get("updated", ""))
            contradiction = {
                "id": newest_other.get("id"),
                "text": newest_other.get("text"),
                "topic": newest_other.get("topic"),
                "created": newest_other.get("created"),
                "updated": newest_other.get("updated"),
            }

        entry = _new_entry(text, kind, wanted_topic, source, auto, now)
        entries.append(entry)
        _prune(store)
        _save(store, dir_override)
    return {"ok": True, "result": {"action": "saved", "entry": entry,
                                   "contradiction": contradiction}}


def edit(
    entry_id: str,
    *,
    text: str | None = None,
    topic: str | None = None,
    kind: str | None = None,
    clear_topic: bool = False,
    dir_override: str | Path | None = None,
) -> dict[str, Any]:
    """Edit one entry by id (owner review workflow).

    New text is redacted like any save; if the edited text now duplicates
    another entry of the same kind, the two are merged (confirmations bump)
    instead of leaving a contradiction that isn't one.
    """
    entry_id = str(entry_id).strip()
    if not entry_id:
        raise MemoryError("edit يحتاج entry_id")
    if kind is not None and kind not in KINDS:
        raise MemoryError(f"kind must be one of {KINDS}, got {kind!r}")
    with _LOCK:
        store = _load(dir_override)
        target = next((e for e in store["entries"] if e.get("id") == entry_id), None)
        if target is None:
            raise MemoryError(f"no memory entry with id {entry_id!r}")
        old = {k: target.get(k) for k in ("id", "kind", "text", "topic", "updated")}
        merged_into: str | None = None
        if text is not None:
            new_text = redact_secrets(str(text).strip())[:MAX_TEXT_CHARS]
            if not new_text:
                raise MemoryError("لا يمكن حفظ ذاكرة فارغة - text is empty")
            duplicate = next(
                (e for e in store["entries"]
                 if e is not target
                 and e.get("kind") == (kind or target.get("kind"))
                 and _normalize(str(e.get("text", ""))) == _normalize(new_text)),
                None,
            )
            if duplicate is not None:
                duplicate["confirmations"] = int(duplicate.get("confirmations", 1)) + 1
                duplicate["updated"] = _now_iso()
                store["entries"] = [e for e in store["entries"] if e is not target]
                _save(store, dir_override)
                return {"ok": True, "result": {"action": "merged",
                                               "merged_into": duplicate["id"],
                                               "old": old, "entry": duplicate}}
            target["text"] = new_text
        if kind is not None:
            target["kind"] = kind
        if clear_topic:
            target["topic"] = None
        elif topic is not None:
            target["topic"] = topic.strip() or None
        target["updated"] = _now_iso()
        _save(store, dir_override)
    return {"ok": True, "result": {"action": "edited", "old": old, "entry": target}}


def forget(
    entry_id: str = "",
    match_text: str = "",
    topic: str | None = None,
    kind: str = "",
    dir_override: str | Path | None = None,
) -> dict[str, Any]:
    """Remove entries matching the given selectors (at least one required)."""
    entry_id = entry_id.strip()
    match_text = _normalize(match_text)
    topic = (topic or "").strip()
    kind = kind.strip()
    if not any([entry_id, match_text, topic, kind]):
        raise MemoryError(
            "forget يحتاج معيارًا واحدًا على الأقل: entry_id أو match_text أو topic أو kind"
        )
    with _LOCK:
        store = _load(dir_override)
        def matches(entry: dict[str, Any]) -> bool:
            if entry_id and entry.get("id") != entry_id:
                return False
            if match_text and match_text not in _normalize(str(entry.get("text", ""))):
                return False
            if topic and (entry.get("topic") or "").casefold() != topic.casefold():
                return False
            if kind and entry.get("kind") != kind:
                return False
            return True
        removed = [e for e in store["entries"] if matches(e)]
        if not removed:
            return {"ok": True, "result": {"action": "noop", "removed": [], "count": 0}}
        store["entries"] = [e for e in store["entries"] if not matches(e)]
        _save(store, dir_override)
    return {"ok": True, "result": {"action": "forgotten",
                                   "removed": [{"id": e.get("id"), "text": e.get("text")}
                                               for e in removed],
                                   "count": len(removed)}}


def recall(
    query: str = "",
    topic: str | None = None,
    kind: str = "",
    limit: int = 50,
    dir_override: str | Path | None = None,
    semantic: bool = True,
) -> dict[str, Any]:
    """Return matching entries, newest-first.

    Hybrid ranking: keyword hits (exact words) plus, when a local embedder is
    reachable, semantic similarity (cosine >= AALI_SEMANTIC_MIN) so related
    phrasings surface even without shared words. ``result["mode"]`` honestly
    reports "hybrid" or "keyword".
    """
    query = _normalize(query or "")
    topic = (topic or "").strip()
    kind = (kind or "").strip()
    entries = _load(dir_override)["entries"]

    filtered = []
    for entry in entries:
        if kind and entry.get("kind") != kind:
            continue
        if topic and (entry.get("topic") or "").casefold() != topic.casefold():
            continue
        filtered.append(entry)

    words = [w for w in query.split() if len(w) > 1] if query else []
    keyword_scored: list[tuple[float, dict[str, Any]]] = []
    for entry in filtered:
        if words:
            hay = _normalize(f"{entry.get('text', '')} {entry.get('topic') or ''}")
            hits = sum(1 for w in words if w in hay)
            if hits == 0:
                continue
            keyword_scored.append((float(hits), entry))
        else:
            keyword_scored.append((0.0, entry))

    scored = keyword_scored
    mode = "keyword"
    if words and semantic:
        query_embedding = _ollama_embed([query])
        if query_embedding:
            vectors = _ensure_vectors(filtered, dir_override)
            if vectors:
                mode = "hybrid"
                query_vector = query_embedding[0]
                bonuses: dict[str, float] = {}
                for entry in filtered:
                    vector = vectors.get(str(entry.get("id", "")))
                    if not vector:
                        continue
                    similarity = _cosine(query_vector, vector)
                    if similarity >= _SEMANTIC_MIN:
                        bonuses[str(entry.get("id"))] = _SEMANTIC_WEIGHT * max(0.0, similarity)
                merged: dict[str, tuple[float, dict[str, Any]]] = {}
                for score, entry in keyword_scored:
                    entry_id = str(entry.get("id", ""))
                    merged[entry_id] = (score + bonuses.pop(entry_id, 0.0), entry)
                for entry_id, bonus in bonuses.items():
                    entry = next(e for e in filtered if str(e.get("id", "")) == entry_id)
                    merged[entry_id] = (bonus, entry)
                scored = list(merged.values())

    scored.sort(key=lambda pair: (pair[0], pair[1].get("updated", "")), reverse=True)
    selected = [entry for _, entry in scored[: max(1, int(limit))]]
    return {"ok": True, "result": {"entries": selected, "count": len(selected),
                                   "total": len(entries), "mode": mode}}


def get(entry_id: str, dir_override: str | Path | None = None) -> dict[str, Any]:
    """Return one entry by id, or raise MemoryError if missing."""
    entry_id = str(entry_id).strip()
    if not entry_id:
        raise MemoryError("get يحتاج entry_id")
    entry = next((e for e in _load(dir_override)["entries"] if e.get("id") == entry_id), None)
    if entry is None:
        raise MemoryError(f"no memory entry with id {entry_id!r}")
    return {"ok": True, "result": {"entry": entry}}


def summary(dir_override: str | Path | None = None) -> dict[str, Any]:
    entries = _load(dir_override)["entries"]
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.get("kind", "?")] = counts.get(entry.get("kind", "?"), 0) + 1
    latest = sorted(entries, key=lambda e: e.get("updated", ""), reverse=True)[:5]
    return {"ok": True, "result": {
        "total": len(entries), "by_kind": counts,
        "latest": [{"id": e.get("id"), "kind": e.get("kind"),
                    "text": str(e.get("text", ""))[:120],
                    "topic": e.get("topic"), "updated": e.get("updated")}
                   for e in latest],
    }}


# ---------------------------------------------------------------------------
# conversation log (raw turns for mentor-SFT + the 07:00/19:00 timeline)
# ---------------------------------------------------------------------------

def record_turn(
    role: str,
    text: str,
    dir_override: str | Path | None = None,
) -> dict[str, Any]:
    """Append one raw conversation turn. Never raises into the caller.

    User messages matching clear directive phrases are ALSO auto-captured into
    long-term memory (marked auto=True) so "don't ever do X" survives even if
    the model forgets to call memory(save).
    """
    try:
        text = str(text).strip()
        if not text or role not in ("user", "assistant"):
            return {"ok": False}
        path = conversation_log_path(dir_override)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": _now_iso(), "role": role, "text": text[:MAX_LOG_CHARS]}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if role == "user" and _AUTO_CAPTURE_RE.search(text):
            first_sentence = re.split(r"(?<=[.!؟?])\s+", text)[0][:300]
            try:
                remember(first_sentence, kind="decision", auto=True,
                         dir_override=dir_override)
            except MemoryError:
                pass
        return {"ok": True}
    except OSError:
        return {"ok": False}


# ---------------------------------------------------------------------------
# system-prompt rendering
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# semantic recall (embedding-based, hybrid with keyword matching)
# ---------------------------------------------------------------------------
# Owner request (2026-09-06): "what did I say about deployments" must find
# "never push to GitHub without asking" - keyword matching can't bridge that.
# Entries are embedded with a tiny local embedder (Ollama nomic-embed-text,
# CPU-friendly) and cached in a sidecar file keyed by entry id + updated ts,
# so vectors are computed once per entry, never per query-time storm.
# When no embedder is reachable, recall silently stays keyword-only -
# the result honestly reports which mode ran.

_EMBED_MODEL = os.getenv("AALI_EMBED_MODEL", "nomic-embed-text")
_EMBED_TIMEOUT = float(os.getenv("AALI_EMBED_TIMEOUT", "4"))
_SEMANTIC_MIN = float(os.getenv("AALI_SEMANTIC_MIN", "0.35"))
_SEMANTIC_WEIGHT = 2.0
_VECTOR_SIDECAR_NAME = "aali_memory_vectors.json"
_EMBED_STATE: dict[str, bool | None] = {"available": None}


def _sidecar_path(dir_override: str | Path | None = None) -> Path:
    return memory_dir(dir_override) / _VECTOR_SIDECAR_NAME


def _load_sidecar(dir_override: str | Path | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(_sidecar_path(dir_override).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "vectors": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("vectors"), dict):
        return {"version": 1, "vectors": {}}
    return payload


def _save_sidecar(payload: dict[str, Any], dir_override: str | Path | None = None) -> None:
    path = _sidecar_path(dir_override)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _ollama_embed(texts: list[str]) -> list[list[float]] | None:
    """One batched /api/embed call, or None when no embedder is reachable."""
    if os.getenv("AALI_EMBEDDINGS", "1") == "0":
        return None
    if _EMBED_STATE["available"] is False:
        return None
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": _EMBED_MODEL, "input": texts},
            timeout=_EMBED_TIMEOUT,
        )
        if not response.ok:
            _EMBED_STATE["available"] = False
            return None
        embeddings = response.json().get("embeddings")
        if (isinstance(embeddings, list) and embeddings
                and isinstance(embeddings[0], list)):
            _EMBED_STATE["available"] = True
            return embeddings
        _EMBED_STATE["available"] = False
        return None
    except Exception:  # noqa: BLE001 - connection refused / timeout / etc.
        _EMBED_STATE["available"] = False
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _ensure_vectors(
    entries: list[dict[str, Any]],
    dir_override: str | Path | None = None,
) -> dict[str, list[float]]:
    """id -> vector for as many entries as possible; batch-embeds missing ones
    and prunes vectors of entries that no longer exist."""
    sidecar = _load_sidecar(dir_override)
    stored = sidecar.setdefault("vectors", {})
    vectors: dict[str, list[float]] = {}
    missing: list[dict[str, Any]] = []
    live_ids: set[str] = set()
    for entry in entries:
        entry_id = str(entry.get("id", ""))
        live_ids.add(entry_id)
        hit = stored.get(entry_id)
        if (isinstance(hit, dict) and hit.get("model") == _EMBED_MODEL
                and hit.get("updated") == entry.get("updated")
                and isinstance(hit.get("vector"), list)):
            vectors[entry_id] = hit["vector"]
        else:
            missing.append(entry)
    if missing:
        texts = [
            f"{entry.get('kind', '')}: {entry.get('text', '')}"
            f" ({entry.get('topic') or ''})"
            for entry in missing
        ]
        embeddings = _ollama_embed(texts)
        if embeddings is not None and len(embeddings) == len(missing):
            for entry, vector in zip(missing, embeddings):
                entry_id = str(entry.get("id", ""))
                vectors[entry_id] = vector
                stored[entry_id] = {
                    "updated": entry.get("updated"),
                    "model": _EMBED_MODEL,
                    "vector": vector,
                }
            # Prune vectors for entries that no longer exist, then persist.
            for stale in [k for k in stored if k not in live_ids]:
                stored.pop(stale, None)
            _save_sidecar(sidecar, dir_override)
    return vectors


def render_block(
    dir_override: str | Path | None = None,
    max_entries: int = MAX_RENDER_ENTRIES,
) -> str:
    """The context block injected into every system prompt ("" when empty)."""
    try:
        entries = _load(dir_override)["entries"]
    except Exception:  # noqa: BLE001 - memory must never break a request
        return ""
    if not entries:
        return ""
    newest_first = sorted(entries, key=lambda e: e.get("updated", ""), reverse=True)
    lines = []
    for entry in newest_first[: max(1, int(max_entries))]:
        when = str(entry.get("updated", ""))[:16].replace("T", " ")
        scope = f" • topic: {entry['topic']}" if entry.get("topic") else ""
        text = str(entry.get("text", ""))[:300]
        lines.append(f"- [{when}] ({entry.get('kind')}{scope}) \"{text}\"")
    return (
        "\n### ذاكرة طويلة الأمد عن هذا المستخدم / Long-term memory about this user "
        "(newest first)\n"
        "These are things the user actually told you in earlier conversations, with "
        "their dates - treat them as facts about the user's wishes. If a newer entry "
        "contradicts an older one on the same topic, FOLLOW THE NEWEST and tell the "
        "user what changed (\"سابقًا طلبت X، والآن طلبت غير ذلك\"). Never deny knowing "
        "something that is listed here.\n"
        + "\n".join(lines)
        + "\n"
    )
