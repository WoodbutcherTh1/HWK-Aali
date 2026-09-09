"""Account platform tests — users | builders & team (admin, owner).

One app, two roles: anyone signs up with email+password+code; emails on the
admins list get role=admin and can open /api/admin/* with their session token.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

from file_agent import accounts  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    path = Path(tempfile.mkdtemp()) / "acc_test.jsonl"
    accounts._configure(path)
    accounts._accounts.clear()
    accounts._pending.clear()
    accounts._sessions.clear()
    yield path


def test_full_user_lifecycle(store):
    r = accounts.signup("user@example.com", "password123")
    assert r["ok"] and r["role"] == "user"
    v = accounts.verify("user@example.com", r["dev_code"])
    assert v["ok"] and v["role"] == "user"
    l = accounts.login("user@example.com", "password123")
    assert l["ok"] and l["role"] == "user"
    s = accounts.session(l["token"])
    assert s and s["email"] == "user@example.com" and not s["is_admin"]


def test_admin_email_gets_admin_role(store):
    r = accounts.signup("hmam@hwk.team", "password123")
    assert r["role"] == "admin"
    accounts.verify("hmam@hwk.team", r["dev_code"])
    l = accounts.login("hmam@hwk.team", "password123")
    assert l["role"] == "admin"
    assert accounts.session(l["token"])["is_admin"]


def test_admin_env_override(store, monkeypatch):
    monkeypatch.setenv("AALI_ADMIN_EMAILS", "builder2@hwk.team")
    r = accounts.signup("builder2@hwk.team", "password123")
    assert r["role"] == "admin"
    r2 = accounts.signup("other@example.com", "password123")
    assert r2["role"] == "user"


def test_duplicate_signup_rejected(store):
    r = accounts.signup("dup@example.com", "password123")
    accounts.verify("dup@example.com", r["dev_code"])
    again = accounts.signup("dup@example.com", "password123")
    assert not again["ok"]


def test_wrong_code_rejected(store):
    r = accounts.signup("code@example.com", "password123")
    bad = accounts.verify("code@example.com", "000000" if r["dev_code"] != "000000" else "111111")
    assert not bad["ok"]


def test_wrong_password_rejected(store):
    r = accounts.signup("pw@example.com", "password123")
    accounts.verify("pw@example.com", r["dev_code"])
    l = accounts.login("pw@example.com", "wrongpass99")
    assert not l["ok"]


def test_short_password_rejected(store):
    assert not accounts.signup("short@example.com", "short")["ok"]


def test_reset_flow(store):
    r = accounts.signup("reset@example.com", "oldpassword1")
    accounts.verify("reset@example.com", r["dev_code"])
    rq = accounts.reset_request("reset@example.com")
    assert rq["ok"]
    rc = accounts.reset_confirm("reset@example.com", rq["dev_code"], "newpassword9")
    assert rc["ok"]
    assert accounts.login("reset@example.com", "oldpassword1")["ok"] is False
    assert accounts.login("reset@example.com", "newpassword9")["ok"]


def test_reset_does_not_leak_unknown_emails(store):
    rq = accounts.reset_request("ghost@example.com")
    assert rq["ok"] and "dev_code" not in rq


def test_reset_kills_sessions(store):
    r = accounts.signup("kill@example.com", "oldpassword1")
    accounts.verify("kill@example.com", r["dev_code"])
    l = accounts.login("kill@example.com", "oldpassword1")
    assert accounts.session(l["token"])
    rq = accounts.reset_request("kill@example.com")
    accounts.reset_confirm("kill@example.com", rq["dev_code"], "newpassword9")
    assert accounts.session(l["token"]) is None


def test_logout_invalidates(store):
    r = accounts.signup("out@example.com", "password123")
    accounts.verify("out@example.com", r["dev_code"])
    l = accounts.login("out@example.com", "password123")
    accounts.logout(l["token"])
    assert accounts.session(l["token"]) is None


def test_remove_account(store):
    r = accounts.signup("gone@example.com", "password123")
    accounts.verify("gone@example.com", r["dev_code"])
    assert accounts.remove_account("gone@example.com")
    assert not accounts.login("gone@example.com", "password123")["ok"]
    assert not accounts.remove_account("gone@example.com")


def test_unverified_login_blocked(store):
    accounts.signup("unv@example.com", "password123")  # never verified
    assert not accounts.login("unv@example.com", "password123")["ok"]
