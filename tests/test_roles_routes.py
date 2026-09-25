"""Track B 11.6 + 11.7 (2026-09-25): role-gated system routes and the
owner preview mode ("view as user").

Contract under test:
- /api/system/training -> owner only; /api/system/{models,logs,health_full}
  -> owner+dev; EVERYONE else gets the uniform 404 (existence hidden).
- Log tails are whitelisted, tail-capped, and secret-redacted.
- Preview: owner-grade callers can downgrade THEIR OWN session to `user`;
  the flag is server-side (client headers cannot set/clear it); user/guest
  get 404 on the control routes; exiting restores the real role.
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import accounts  # noqa: E402
from file_agent import apikeys  # noqa: E402
from file_agent import audit  # noqa: E402
from file_agent import preview  # noqa: E402
from file_agent import roles as roles_mod  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts._configure(tmp_path / "acc.jsonl")
    apikeys._configure(tmp_path / "keys.jsonl")
    audit._configure(tmp_path / "audit.jsonl")
    accounts._accounts.clear()
    accounts._pending.clear()
    accounts._sessions.clear()
    preview.master_stop()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _admin_session(client, email="hmam@hwk.team", password="password123"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    body = r.get_json() or {}
    if body.get("dev_code"):
        client.post("/api/auth/verify", json={"email": email, "code": body["dev_code"]})
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.get_json()["token"]


def _user_session(client, email="plain@example.com", password="password123"):
    return _admin_session(client, email, password)  # non-admin default email


def _sess_h(token):
    return {"X-Session-Token": token}


def _key_h():
    return {"X-API-Key": MASTER}


# ————————————————————————————— 11.6: role-gated system routes —————————————————————————————

def test_training_owner_only(client):
    assert client.get("/api/system/training", headers=_key_h()).status_code == 200
    assert client.get("/api/system/training").status_code == 404  # remote anon


def test_training_hidden_from_user_and_admin(client):
    user_tok = _user_session(client)
    assert client.get("/api/system/training", headers=_sess_h(user_tok)).status_code == 404
    admin_tok = _admin_session(client)
    assert client.get("/api/system/training", headers=_sess_h(admin_tok)).status_code == 404


def test_models_logs_health_owner_and_dev(client, monkeypatch):
    monkeypatch.setenv("AALI_DEV_EMAILS", "dev@hwk.team")
    dev_tok = _admin_session(client, "dev@hwk.team")
    for path in ("/api/system/models", "/api/system/logs",
                 "/api/system/health_full"):
        assert client.get(path, headers=_key_h()).status_code == 200, path
        assert client.get(path, headers=_sess_h(dev_tok)).status_code == 200, path
        user_tok = _user_session(client)
        assert client.get(path, headers=_sess_h(user_tok)).status_code == 404, path
        assert client.get(path).status_code == 404, path


def test_training_board_shape(client, monkeypatch):
    fake = SimpleNamespace(snapshot=lambda: {
        "generated": "t", "alerts": 0,
        "rows": [{"section": "GPU", "icon": "✅", "text": "idle"}]})
    monkeypatch.setitem(sys.modules, "aali_jobs", fake)
    body = client.get("/api/system/training", headers=_key_h()).get_json()
    assert body["ok"] and body["jobs"]["rows"][0]["section"] == "GPU"


def test_logs_whitelist_and_redaction(client, monkeypatch):
    log = Path(tempfile.mkdtemp()) / "x.log"
    log.write_text("boot ok\nkey=sk-abcdefghij1234567890\nend\n", encoding="utf-8")
    monkeypatch.setitem(app_module._LOG_FILES, "aali_server", str(log))
    body = client.get("/api/system/logs?file=aali_server&tail=10",
                      headers=_key_h()).get_json()
    assert body["exists"] and len(body["lines"]) == 3
    assert not any("sk-abcdefghij" in ln for ln in body["lines"])
    assert client.get("/api/system/logs?file=../etc/passwd",
                      headers=_key_h()).status_code == 400
    assert client.get("/api/system/logs?file=nope",
                      headers=_key_h()).status_code == 400


def test_health_full_extends_public_health(client):
    pub = client.get("/api/health").get_json()
    full = client.get("/api/system/health_full", headers=_key_h()).get_json()
    assert full["ok"] and full["full"] is True
    assert full["mode"] == pub["mode"]
    assert "uptime_s" in full and "tts_available" in full


def test_whoami_raw_dev_only(client, monkeypatch):
    monkeypatch.setenv("AALI_DEV_EMAILS", "dev@hwk.team")
    dev_tok = _admin_session(client, "dev@hwk.team")
    r = client.get("/api/auth/whoami_raw", headers=_sess_h(dev_tok))
    assert r.status_code == 200
    assert r.get_json()["raw"]["role"] == "dev"
    assert client.get("/api/auth/whoami_raw").status_code == 404


# ————————————————————————————— 11.7: owner preview —————————————————————————————

def test_preview_module_flips_session_flag(client):
    tok = _admin_session(client)
    assert preview.active(tok) is False
    assert preview.start(tok)["previewing"] is True
    assert preview.active(tok) is True
    preview.stop(tok)
    assert preview.active(tok) is False


def test_preview_requires_privileged_role(client):
    user_tok = _user_session(client)
    assert preview.start(user_tok) is None  # role < admin: no preview surface
    assert preview.active(user_tok) is False


def test_preview_control_routes_404_for_user_and_remote(client):
    user_tok = _user_session(client)
    assert client.post("/api/auth/preview", headers=_sess_h(user_tok)).status_code == 404
    assert client.delete("/api/auth/preview", headers=_sess_h(user_tok)).status_code == 404
    assert client.post("/api/auth/preview").status_code == 404


def test_preview_downgrades_admin_session_server_side(client):
    tok = _admin_session(client)
    me0 = client.get("/api/auth/me", headers=_sess_h(tok)).get_json()
    assert me0["aali"]["role"] == "admin"
    assert client.post("/api/auth/preview", headers=_sess_h(tok)).get_json()["previewing"] is True
    me1 = client.get("/api/auth/me", headers=_sess_h(tok)).get_json()
    assert me1["aali"]["role"] == "user" and me1["aali"]["previewing"] is True
    # Gated surfaces vanish DURING preview — same 404 the user would get.
    assert client.get("/api/system/models", headers=_sess_h(tok)).status_code == 404
    # ...and a client CANNOT clear the flag (DELETE with no session = 404;
    # the flag lives server-side, keyed by the real token).
    assert client.delete("/api/auth/preview").status_code == 404
    assert client.delete("/api/auth/preview", headers=_sess_h(tok)).status_code == 200
    me2 = client.get("/api/auth/me", headers=_sess_h(tok)).get_json()
    assert me2["aali"]["role"] == "admin"


def test_master_key_preview_window(client):
    r = client.post("/api/auth/preview", headers=_key_h(),
                    json={"ttl": 60})
    assert r.status_code == 200 and r.get_json()["previewing"] is True
    me = client.get("/api/auth/me", headers=_key_h()).get_json()
    assert me["aali"]["role"] == "user" and me["aali"]["previewing"] is True
    assert client.get("/api/system/models", headers=_key_h()).status_code == 404
    assert client.delete("/api/auth/preview", headers=_key_h()).status_code == 200
    me2 = client.get("/api/auth/me", headers=_key_h()).get_json()
    assert me2["aali"]["role"] == "owner"


def test_preview_never_elevates(client):
    """The downgrade target is `user` — a dev previewing must never gain
    owner powers, and a guest (404 path) can never start one."""
    assert not roles_mod.at_least("user", "dev")
    out = roles_mod.permissions_for("user")
    assert "training" not in out and "models" not in out
