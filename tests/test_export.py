"""HTTP tests for GET /api/session/<sid>/export (Wave 1 #1, 2026-09-25).

Markdown is the default (no-dep) format; JSON is a faithful record dump;
pdf answers 400 honestly until implemented. Session isolation must hold:
_user_ns() scopes every lookup to the calling key.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    monkeypatch.setattr(
        app_module, "SESSIONS_FILE", tmp_path / "sessions.jsonl")
    app_module._sessions.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed(sid: str, turns: list[dict], key: str | None = None) -> None:
    app_module._sessions[key or f"u{MASTER}:{sid}"] = {
        "sid": sid,
        "key": key or f"u{MASTER}:{sid}",
        "created_at": 1727200000.0,
        "updated_at": 1727200100.0,
        "turns": turns,
    }


def _headers() -> dict[str, str]:
    return {"X-API-Key": MASTER}


def test_markdown_default_export(client):
    _seed("s1", [
        {"role": "user", "content": "مرحبا آلي", "ts": 1727200001.0},
        {"role": "assistant", "content": "أهلاً بك!", "ts": 1727200002.0},
    ])
    r = client.get("/api/session/s1/export", headers=_headers())
    assert r.status_code == 200
    assert "text/markdown" in r.headers["Content-Type"]
    assert "attachment" in r.headers.get("Content-Disposition", "")
    body = r.get_data(as_text=True)
    assert "تصدير محادثة" in body
    assert "## أنا" in body
    assert "## آلي" in body
    assert "مرحبا آلي" in body
    assert "أهلاً بك!" in body


def test_json_export_faithful_dump(client):
    turns = [
        {"role": "user", "content": "hello", "ts": 1727200001.0},
        {"role": "assistant", "content": "hi there", "ts": 1727200002.0},
    ]
    _seed("s2", turns)
    r = client.get("/api/session/s2/export?format=json", headers=_headers())
    assert r.status_code == 200
    assert "application/json" in r.headers["Content-Type"]
    data = json.loads(r.get_data(as_text=True))
    assert data["sid"] == "s2"
    assert data["turns"] == turns


def test_pdf_answers_400_honestly(client):
    _seed("s3", [{"role": "user", "content": "x", "ts": 0}])
    r = client.get("/api/session/s3/export?format=pdf", headers=_headers())
    assert r.status_code == 400
    assert "pdf" in r.get_json()["error"].lower()


def test_unknown_session_404(client):
    r = client.get("/api/session/nope/export", headers=_headers())
    assert r.status_code == 404


def test_export_is_session_scoped(client):
    """Another key's session must be invisible (multi-user isolation)."""
    _seed("s4", [{"role": "user", "content": "secret", "ts": 0}],
          key="uother-key:s4")
    r = client.get("/api/session/s4/export", headers=_headers())
    assert r.status_code == 404


def test_export_requires_key_in_key_mode(client):
    _seed("s5", [{"role": "user", "content": "x", "ts": 0}])
    # Track B 11.2: unauthenticated callers get the uniform 404.
    r = client.get("/api/session/s5/export")
    assert r.status_code == 404


def test_non_chat_roles_skipped_in_markdown(client):
    _seed("s6", [
        {"role": "system", "content": "SYS", "ts": 0},
        {"role": "tool", "content": "TOOL", "ts": 0},
        {"role": "user", "content": "visible", "ts": 0},
    ])
    body = client.get("/api/session/s6/export",
                      headers=_headers()).get_data(as_text=True)
    assert "SYS" not in body
    assert "TOOL" not in body
    assert "visible" in body


def test_empty_session_exports_graceful_markdown(client):
    _seed("s7", [])
    r = client.get("/api/session/s7/export", headers=_headers())
    assert r.status_code == 200
    assert "تصدير محادثة" in r.get_data(as_text=True)
