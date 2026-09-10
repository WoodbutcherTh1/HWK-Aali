"""Unit tests for the admin audit log (file_agent/audit.py)."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

from file_agent import audit  # noqa: E402


@pytest.fixture(autouse=True)
def _store(tmp_path):
    audit._configure(tmp_path / "audit.jsonl")
    yield
    audit.clear()


def test_record_writes_and_list_returns_newest_first():
    audit.record("key_issue", target="k1", actor="a@x", actor_kind="session", ip="10.0.0.1")
    audit.record("key_revoke", target="k1", actor="a@x", actor_kind="session", ip="10.0.0.1")
    events = audit.list_events()
    assert [e["action"] for e in events] == ["key_revoke", "key_issue"]
    assert events[0]["target"] == "k1"
    assert events[0]["ip"] == "10.0.0.1"
    assert events[0]["actor_kind"] == "session"


def test_record_never_raises_and_survives_bad_store(tmp_path, monkeypatch):
    audit._configure(tmp_path / "no-dir" / "deep" / "audit.jsonl")  # mkdir makes it work
    assert audit.record("key_issue", target="k") is not None
    # An unwritable path fails softly.
    audit._configure(tmp_path / "audit.jsonl")
    monkeypatch.setattr(Path, "open", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert audit.record("key_issue", target="k") is None
    assert audit.list_events() == []


def test_list_tolerates_corrupted_lines(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text(
        json.dumps({"ts": 1, "action": "key_issue", "target": "a"}) + "\n"
        + "not-json-at-all\n"
        + json.dumps({"ts": 2, "action": "key_revoke", "target": "b"}) + "\n",
        encoding="utf-8",
    )
    events = audit.list_events()
    assert [e["target"] for e in events] == ["b", "a"]


def test_action_filter():
    audit.record("key_issue", target="a")
    audit.record("account_delete", target="u@example.com")
    audit.record("key_issue", target="b")
    events = audit.list_events(action="key_issue")
    assert [e["target"] for e in events] == ["b", "a"]


def test_limit_and_trim():
    for i in range(25):
        audit.record("key_issue", target=f"k{i}")
    assert len(audit.list_events(limit=5)) == 5
    # Newest survive the trim: the file keeps only the tail.
    kept = [json.loads(l) for l in (audit.store_path() and open(audit.store_path(), encoding="utf-8"))]
    assert len(kept) <= audit._MAX_LINES


def test_fields_are_capped_and_typed():
    audit.record("key_issue", target="x" * 500, detail="d" * 500, ip="i" * 500, actor="a" * 500)
    e = audit.list_events()[0]
    assert len(e["target"]) == 200
    assert len(e["detail"]) == 300
    assert len(e["ip"]) == 80
    assert len(e["actor"]) == 120
    assert isinstance(e["ts"], float) and e["ts"] <= time.time() + 1
