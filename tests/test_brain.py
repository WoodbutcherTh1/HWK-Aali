"""Tests for the owner-only آلي Brain Tree (structure, renderers, gating)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import brain_tree  # noqa: E402


def test_tree_covers_the_owner_flows() -> None:
    """The flows the owner explicitly asked to see must exist."""
    for key in ("signin", "signup", "chat", "output", "key"):
        assert key in brain_tree.FLOWS, f"missing flow: {key}"
    # every flow has steps with title + description
    for flow in brain_tree.FLOWS.values():
        assert flow["steps"], flow["title"]
        for step in flow["steps"]:
            assert step.get("t") and step.get("d")


def test_every_step_cites_real_code() -> None:
    """Each step names the code file that implements it — no fiction."""
    for flow in brain_tree.FLOWS.values():
        for step in flow["steps"]:
            assert step.get("c"), f"step without code ref: {step.get('t')}"


def test_sources_and_security_present() -> None:
    assert "memory" in brain_tree.SOURCES and "model" in brain_tree.SOURCES
    assert len(brain_tree.SECURITY_NOTES) >= 5


def test_ascii_renderer() -> None:
    out = brain_tree.render_ascii()
    assert "شجرة العقل" in out and "الذاكرة الدائمة" in out


def test_html_renders_live_snapshot() -> None:
    live = brain_tree.live_snapshot()
    html = brain_tree.render_html(live)
    assert "شجرة عقل آلي" in html
    assert "الحالة الحية" in html
    assert live["active_brain"]  # a brain is always named


def test_obsidian_vault_links(tmp_path) -> None:
    written = brain_tree.render_obsidian(tmp_path)
    names = {p.name for p in written}
    assert "ROOT.md" in names
    root = (tmp_path / "ROOT.md").read_text(encoding="utf-8")
    for key in brain_tree.FLOWS:
        fname = brain_tree.flow_file_name(key)
        assert fname in names, f"missing note {fname}"
        assert f"[[{fname}" in root  # ROOT links every flow
    # cross-links inside flow notes
    chat = (tmp_path / brain_tree.flow_file_name("chat")).read_text(encoding="utf-8")
    assert "[[" in chat


def test_brain_page_is_admin_only(monkeypatch) -> None:
    """Multi-user mode: /brain must 401 without the admin key, 200 with it."""
    monkeypatch.setenv("AALI_API_KEY", "brain-test-master")
    import importlib
    import app as aali_app
    importlib.reload(aali_app)
    client = aali_app.app.test_client()
    assert client.get("/brain").status_code == 401
    assert client.get("/api/brain/live").status_code == 401
    ok = client.get("/brain", headers={"X-API-Key": "brain-test-master"})
    assert ok.status_code == 200
    assert "شجرة عقل آلي" in ok.get_data(as_text=True)
    live = client.get("/api/brain/live", headers={"X-API-Key": "brain-test-master"})
    assert live.status_code == 200
    assert live.get_json()["ok"] is True
    monkeypatch.delenv("AALI_API_KEY")
    importlib.reload(aali_app)
