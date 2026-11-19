"""Tests for file_agent/proposals.py and the approval API."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import proposals  # noqa: E402


def _stage_new(tmp_path: Path, name: str = "notes.txt",
               text: str = "hello\n") -> dict:
    return proposals.stage_write(name, text, tmp_path)


def test_stage_write_never_touches_the_real_file(tmp_path: Path) -> None:
    out = _stage_new(tmp_path)
    assert out["status"] == "pending_approval"
    assert out["path"] == "notes.txt"
    assert not (tmp_path / "notes.txt").exists()
    assert (tmp_path / ".aali" / "staging").is_dir()
    meta = proposals.get_proposal(out["proposal_id"], tmp_path)
    assert meta["status"] == "pending"
    assert "+hello" in meta["diff"]
    assert "notes.txt" in out["message"]


def test_apply_writes_once_and_cleans_payloads(tmp_path: Path) -> None:
    out = _stage_new(tmp_path, text="line one\n")
    applied = proposals.apply_proposal(out["proposal_id"], tmp_path)
    assert applied["status"] == "applied"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "line one\n"
    payloads = list((tmp_path / ".aali" / "staging").glob(f"{out['proposal_id']}.*"))
    assert [p.suffix for p in payloads] == [".json"]
    with pytest.raises(proposals.ProposalError):
        proposals.apply_proposal(out["proposal_id"], tmp_path)


def test_apply_refuses_to_clobber_a_changed_file(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("one\n", encoding="utf-8")
    out = proposals.stage_write("notes.txt", "proposed\n", tmp_path,
                                overwrite=True)
    target.write_text("two\n", encoding="utf-8")
    with pytest.raises(proposals.ProposalConflict):
        proposals.apply_proposal(out["proposal_id"], tmp_path)
    assert target.read_text(encoding="utf-8") == "two\n"


def test_reject_writes_nothing(tmp_path: Path) -> None:
    out = _stage_new(tmp_path)
    rejected = proposals.reject_proposal(out["proposal_id"], tmp_path,
                                         reason="not wanted")
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "not wanted"
    assert not (tmp_path / "notes.txt").exists()
    listing = proposals.list_proposals(tmp_path)
    assert listing["pending"] == 0
    assert listing["proposals"][0]["status"] == "rejected"


def test_append_and_replace_stage_the_full_candidate(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("value = 'blue'\n", encoding="utf-8")
    appended = proposals.stage_append("code.py", "print(value)\n", tmp_path)
    proposals.apply_proposal(appended["proposal_id"], tmp_path)
    assert target.read_text(encoding="utf-8") == "value = 'blue'\nprint(value)\n"

    replaced = proposals.stage_replace("code.py", "'blue'", "'green'", tmp_path)
    proposals.apply_proposal(replaced["proposal_id"], tmp_path)
    assert "green" in target.read_text(encoding="utf-8")


def test_proposal_mode_scopes_agent_writes(tmp_path: Path) -> None:
    from file_agent import file_tools

    with proposals.proposal_mode():
        out = file_tools.write_file("agent.txt", "agent text", tmp_path)
        assert out["status"] == "pending_approval"
        assert not (tmp_path / "agent.txt").exists()
    # outside the agent run, a direct human/tool write is immediate again.
    written = file_tools.write_file("human.txt", "human text", tmp_path)
    assert written["created"] is True
    assert (tmp_path / "human.txt").exists()


def test_path_escape_is_refused(tmp_path: Path) -> None:
    with pytest.raises(proposals.ProposalError):
        proposals.stage_write("../escape.txt", "x", tmp_path)
    with pytest.raises(proposals.ProposalError):
        proposals.stage_write("C:/Windows/escape.txt", "x", tmp_path)


def test_malformed_id_is_not_found(tmp_path: Path) -> None:
    for bad in ("", "../../etc/passwd", "not-hex", "a" * 15):
        with pytest.raises(proposals.ProposalNotFound):
            proposals.get_proposal(bad, tmp_path)


def test_pending_payload_only_accepts_a_staged_result() -> None:
    assert proposals.pending_payload({
        "ok": True,
        "result": {"status": "pending_approval", "proposal_id": "abc"},
    })["proposal_id"] == "abc"
    assert proposals.pending_payload({"ok": True, "result": {"created": True}}) is None
    assert proposals.pending_payload({"ok": False, "result": {}}) is None


def test_agent_loop_turns_a_pending_edit_into_a_review_message(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_loop as al

    staged = _stage_new(tmp_path)
    pending = {
        "status": "pending_approval",
        "proposal_id": staged["proposal_id"],
        "path": staged["path"],
        "tool": staged["tool"],
        "diff": staged["diff"],
    }

    def fake_agent(*args, **kwargs):
        raise proposals.ProposalPending(pending)

    monkeypatch.setattr(al, "_agent_loop", fake_agent)
    out = al.agent_loop("change notes", tmp_path, mode="local", print_final=False)
    assert staged["proposal_id"] in out
    assert "pending_approval" not in out  # the user sees a review message
    assert "notes.txt" in out


# ---------------------------------------------------------------------------
# HTTP approval routes
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "_proposal_workspace", lambda: tmp_path)
    monkeypatch.setattr(app_mod, "_proposal_allowed", lambda: True)
    app_mod.app.config.update(TESTING=True)
    return app_mod.app.test_client()


def test_approve_route_applies(client, tmp_path: Path) -> None:
    out = _stage_new(tmp_path)
    response = client.post("/api/approve", json={"proposal_id": out["proposal_id"]})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["proposal"]["status"] == "applied"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"


def test_approve_route_reports_conflict(client, tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("one\n", encoding="utf-8")
    out = proposals.stage_write("notes.txt", "two\n", tmp_path, overwrite=True)
    target.write_text("changed\n", encoding="utf-8")
    response = client.post("/api/approve", json={"proposal_id": out["proposal_id"]})
    assert response.status_code == 409
    assert target.read_text(encoding="utf-8") == "changed\n"


def test_proposals_list_and_reject_routes(client, tmp_path: Path) -> None:
    out = _stage_new(tmp_path)
    listing = client.get("/api/proposals").get_json()
    assert listing["ok"] is True and listing["pending"] == 1
    rejected = client.post("/api/reject", json={
        "proposal_id": out["proposal_id"], "reason": "no"})
    assert rejected.status_code == 200
    assert rejected.get_json()["proposal"]["status"] == "rejected"
    assert not (tmp_path / "notes.txt").exists()


def test_approval_is_hidden_from_unauthorized_callers(
        client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app as app_mod

    out = _stage_new(tmp_path)
    monkeypatch.setattr(app_mod, "_proposal_allowed", lambda: False)
    assert client.get("/api/proposals").status_code == 404
    assert client.post("/api/approve",
                       json={"proposal_id": out["proposal_id"]}).status_code == 404
    assert not (tmp_path / "notes.txt").exists()
