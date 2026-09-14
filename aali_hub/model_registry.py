"""Aali Hub model registry — one catalog of every model version.

Tracks checkpoints, promoted adapters, GGUF exports and HuggingFace uploads so
every layer (Hub /v1 surface, admin API, future OpenRouter listing) answers
from one source of truth. SQLite-backed (same store pattern as users_db).
Rows are content-addressable by (model_id, version) — re-registering an
identical pair is idempotent.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["ModelRegistry", "RegistryError", "FORMATS"]

FORMATS = ("pt", "safetensors", "gguf")

_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class RegistryError(Exception):
    """Expected model-registry error."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    model_id            TEXT NOT NULL,
    version             TEXT NOT NULL,
    path                TEXT NOT NULL,
    format              TEXT NOT NULL,
    context_length      INTEGER NOT NULL DEFAULT 4096,
    tokenizer           TEXT NOT NULL DEFAULT '',
    uploaded_to_hf      INTEGER NOT NULL DEFAULT 0,
    hf_repo             TEXT NOT NULL DEFAULT '',
    openrouter_listed   INTEGER NOT NULL DEFAULT 0,
    promoted            INTEGER NOT NULL DEFAULT 0,
    promoted_at         TEXT,
    eval_score          REAL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (model_id, version)
);
"""


class ModelRegistry:
    """SQLite-backed model version catalog."""

    def __init__(self, db_path: str | Path = "aali_hub.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _validate(model_id: str, version: str, fmt: str) -> None:
        if not _MODEL_ID_RE.match(model_id or ""):
            raise RegistryError(f"bad model_id: {model_id!r}")
        if not version or not isinstance(version, str):
            raise RegistryError("version must be a non-empty string")
        if fmt not in FORMATS:
            raise RegistryError(f"format must be one of {FORMATS}")

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in ("uploaded_to_hf", "openrouter_listed", "promoted"):
            out[key] = bool(out[key])
        return out

    # ------------------------------------------------------------------
    def register(self, model_id: str, version: str, path: str, fmt: str, *,
                 context_length: int = 4096, tokenizer: str = "",
                 promoted: bool = False, eval_score: float | None = None
                 ) -> dict[str, Any]:
        """Register a model version. Idempotent for identical rows."""
        self._validate(model_id, version, fmt)
        if context_length <= 0:
            raise RegistryError("context_length must be positive")
        now = _now_iso()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO models (model_id, version, path, format,"
                    " context_length, tokenizer, promoted, promoted_at,"
                    " eval_score, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (model_id, version, path, fmt, int(context_length),
                     tokenizer, int(promoted),
                     now if promoted else None, eval_score, now))
        except sqlite3.IntegrityError as exc:
            raise RegistryError(
                f"{model_id}@{version} already registered (use update or a "
                "new version)") from exc
        return self.get(model_id, version)

    def get(self, model_id: str, version: str) -> dict[str, Any]:
        """Fetch one model version; raises when missing."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM models WHERE model_id = ? AND version = ?",
                (model_id, version)).fetchone()
        if row is None:
            raise RegistryError(f"{model_id}@{version} not registered")
        return self._public(row)

    def list(self, model_id: str | None = None) -> list[dict[str, Any]]:
        """List versions (all, or of one model_id), newest first."""
        with self._connect() as conn:
            if model_id is None:
                rows = conn.execute(
                    "SELECT * FROM models ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM models WHERE model_id = ?"
                    " ORDER BY created_at DESC", (model_id,)).fetchall()
        return [self._public(r) for r in rows]

    def promoted(self) -> list[dict[str, Any]]:
        """All versions flagged as promoted (candidates for the live brain)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM models WHERE promoted = 1"
                " ORDER BY promoted_at DESC").fetchall()
        return [self._public(r) for r in rows]

    def mark_promoted(self, model_id: str, version: str) -> dict[str, Any]:
        """Flag one version as the promoted one, unflagging its siblings.

        Mirrors the soup pipeline's PROMOTE verdict: exactly one version of
        a model_id carries the promoted flag at any time.
        """
        if not _MODEL_ID_RE.match(model_id or "") or not version:
            raise RegistryError("bad model_id/version")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM models WHERE model_id = ? AND version = ?",
                (model_id, version)).fetchone()
            if row is None:
                raise RegistryError(f"{model_id}@{version} not registered")
            conn.execute(
                "UPDATE models SET promoted = 0, promoted_at = NULL"
                " WHERE model_id = ?", (model_id,))
            conn.execute(
                "UPDATE models SET promoted = 1, promoted_at = ?"
                " WHERE model_id = ? AND version = ?",
                (_now_iso(), model_id, version))
        return self.get(model_id, version)

    def update(self, model_id: str, version: str, *,
               uploaded_to_hf: bool | None = None,
               hf_repo: str | None = None,
               openrouter_listed: bool | None = None,
               eval_score: float | None = None) -> dict[str, Any]:
        """Update distribution/eval flags on a registered version."""
        current = self.get(model_id, version)
        with self._connect() as conn:
            conn.execute(
                "UPDATE models SET uploaded_to_hf = ?, hf_repo = ?,"
                " openrouter_listed = ?, eval_score = ?"
                " WHERE model_id = ? AND version = ?",
                (int(current["uploaded_to_hf"]
                     if uploaded_to_hf is None else uploaded_to_hf),
                 current["hf_repo"] if hf_repo is None else hf_repo,
                 int(current["openrouter_listed"]
                     if openrouter_listed is None else openrouter_listed),
                 current["eval_score"] if eval_score is None else eval_score,
                 model_id, version))
        return self.get(model_id, version)

    def remove(self, model_id: str, version: str) -> None:
        """Drop one version row (admin cleanup; files on disk are untouched)."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM models WHERE model_id = ? AND version = ?",
                (model_id, version))
            if cur.rowcount != 1:
                raise RegistryError(f"{model_id}@{version} not registered")
