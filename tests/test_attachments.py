"""Attachments: users send files/photos/videos with a chat message.

Pins the upload endpoint contract (validation, sandboxed storage, analyze-
first) and the ask-side context injection, without needing the heavy
analyzers (OCR/Whisper run only when tools exist on the machine).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

import app as flask_app  # noqa: E402


def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


# --------------------------------------------------------------- validation
def test_attach_requires_file() -> None:
    res = client().post("/api/attach", data={})
    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_attach_stores_and_analyzes_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(flask_app, "WORKSPACE_ROOT", tmp_path)
    data = {"file": (io.BytesIO("مرحبا يا آلي".encode("utf-8")), "ملاحظة.txt")}
    res = client().post("/api/attach", data=data)
    body = res.get_json()
    assert body["ok"] is True
    assert body["kind"] == "text"
    saved = tmp_path / "uploads" / body["stored"]
    assert saved.is_file()
    assert body["analysis"]["text"] == "مرحبا يا آلي"


def test_attach_sanitizes_dangerous_names(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(flask_app, "WORKSPACE_ROOT", tmp_path)
    data = {"file": (io.BytesIO(b"x"), "../../evil.txt")}
    res = client().post("/api/attach", data=data)
    body = res.get_json()
    assert body["ok"] is True
    assert ".." not in body["stored"]
    assert "/" not in body["stored"] and "\\" not in body["stored"]


def test_ask_rejects_traversal_attachment_names(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(flask_app, "WORKSPACE_ROOT", tmp_path)
    seen: dict = {}

    def fake_loop(message, *a, **kw):
        seen["message"] = message
        return "رد"

    monkeypatch.setattr(flask_app, "agent_loop", fake_loop)
    res = client().post("/api/ask", json={
        "message": "hi", "sid": "s1",
        "attachments": ["../sessions.jsonl", "ok_name.txt"],
    })
    assert res.status_code == 200
    # the traversal name must never reach the loader
    assert "sessions.jsonl" not in seen["message"]


def test_attachment_context_injected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(flask_app, "WORKSPACE_ROOT", tmp_path)
    updir = tmp_path / "uploads"
    updir.mkdir()
    (updir / "abc_note.txt").write_text("القهوة المختصة", encoding="utf-8")
    seen: dict = {}

    def fake_loop(message, *a, **kw):
        seen["message"] = message
        return "رد"

    monkeypatch.setattr(flask_app, "agent_loop", fake_loop)
    client().post("/api/ask", json={
        "message": "ماذا تقسئ؟", "sid": "s2", "attachments": ["abc_note.txt"]})
    assert "القهوة المختصة" in seen["message"]
    assert "uploads/abc_note.txt" in seen["message"]


def test_missing_attachment_is_skipped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(flask_app, "WORKSPACE_ROOT", tmp_path)
    seen: dict = {}

    def fake_loop(message, *a, **kw):
        seen["message"] = message
        return "رد"

    monkeypatch.setattr(flask_app, "agent_loop", fake_loop)
    res = client().post("/api/ask", json={
        "message": "hi", "sid": "s3", "attachments": ["ghost.txt"]})
    assert res.status_code == 200
    assert "ghost.txt" not in seen["message"]


def test_kind_detection() -> None:
    assert flask_app._kind_of(".mp4") == "video"
    assert flask_app._kind_of(".MP3") == "audio"
    assert flask_app._kind_of(".pdf") == "document"
    assert flask_app._kind_of(".png") == "image"
    assert flask_app._kind_of(".py") == "text"
