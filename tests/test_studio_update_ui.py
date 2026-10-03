"""tests/test_studio_update_ui.py — آلي ستوديو's four update surfaces.

The UI is rendered on the SERVER (build-desktop/aali-studio/update_ui.py), so
the owner's four requested surfaces are asserted here as real HTML: the status
bar chip, the top banner, the Settings → «التحديثات» section and the
post-download dialog. No browser, no headless runner, no "the string is in the
JS somewhere" assertion.

The routes are exercised against a REAL loopback hub serving a REAL Ed25519
manifest, because a test that stubs the updater would pass even if the wiring
were backwards.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "build-desktop" / "aali-studio"))

import studio_server  # noqa: E402
import update_ui  # noqa: E402
import updater as su  # noqa: E402
import version as studio_version  # noqa: E402
from shared import updater as shared  # noqa: E402

PRIV = None
PUB_HEX = ""


def _keypair() -> None:
    global PRIV, PUB_HEX
    if PRIV is not None:
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    PRIV = Ed25519PrivateKey.generate()
    PUB_HEX = PRIV.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    _keypair()
    monkeypatch.setenv("AALI_STUDIO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AALI_STUDIO_UPDATE_DIR", str(tmp_path / "install"))
    monkeypatch.setenv("AALI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AALI_STUDIO_UPDATE_PUBKEY", PUB_HEX)
    monkeypatch.delenv("AALI_STUDIO_UPDATE_OFF", raising=False)
    monkeypatch.delenv("AALI_STUDIO_UPDATE_NO_EXIT", raising=False)
    monkeypatch.delenv("AALI_UPDATE_TOKEN", raising=False)
    su._UPDATE.clear()
    su._LAST_CHECK["at"] = 0.0
    su._JOB.update({"phase": "idle", "error": "", "version": "", "started": 0.0,
                    "staged": ""})
    yield
    su._UPDATE.clear()
    su._LAST_CHECK["at"] = 0.0
    su._JOB.update({"phase": "idle", "error": "", "version": "", "started": 0.0,
                    "staged": ""})


# ————— a real signed hub on loopback —————
def make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def sign_hex(message: str) -> str:
    return PRIV.sign(message.encode("utf-8")).hex()


def signed_manifest(artifact: bytes, version: str, platform: str, *,
                    min_version: str = "1.0.0",
                    url: str = "", version_notes: str = "إصدار تجريبي") -> dict:
    from aali_hub.update_server import build_manifest, sign_manifest
    path = url or f"/updates/studio/{version}/download/{platform}"
    digest = hashlib.sha256(artifact).hexdigest()
    manifest = build_manifest(version, min_version, path, digest,
                              release_notes_ar=version_notes,
                              release_notes_en="test release")
    manifest["app"] = "studio"
    manifest["platform"] = platform
    manifest["artifact_signature"] = sign_hex(digest)
    return sign_manifest(manifest, PRIV)


class _Handler(BaseHTTPRequestHandler):
    artifact = b""
    manifest: dict = {}

    def do_GET(self):  # noqa: N802
        if "/download/" in self.path:
            body, kind = self.artifact, "application/zip"
        elif self.path.startswith("/updates/studio/latest"):
            body = json.dumps({"ok": True, "update": self.manifest}).encode()
            kind = "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        return


@pytest.fixture
def hub():
    """A loopback hub publishing a signed 1.1.0 for THIS platform."""
    platform = shared.host_platform()
    payload = su.payload_name()
    artifact = make_zip({payload: "fake-binary-bytes",
                         "VERSION": "1.1.0"})
    _Handler.artifact = artifact
    _Handler.manifest = signed_manifest(artifact, "1.1.0", platform)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    su.save_config({"hub_url": url})
    try:
        yield url, artifact
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def client():
    # NOT entered here on purpose: every test opens it with `with client:` —
    # Flask refuses to nest client invocations, and the existing Studio suite
    # (tests/test_aali_studio.py) uses exactly that shape.
    return studio_server.create_app().test_client()


# ————— [1] the version display —————
def test_the_status_bar_shows_the_current_version():
    html = update_ui.status_bar_html(su.status())
    assert studio_version.version_string() in html
    assert f'v{studio_version.__version__}' in html
    assert 'id="update-status"' in html


def test_version_py_is_the_single_source_for_the_version():
    assert studio_version.__version__ == "1.0.0"
    assert studio_version.BUILD_DATE == "2026-10-03"
    assert studio_version.COMMIT == "auto-generated"
    assert studio_server.ABOUT["version"] == studio_version.__version__
    assert set(studio_version.build_info()) >= {"version", "build_date",
                                                "commit", "app"}


def test_the_status_bar_never_shows_a_tick_it_cannot_back(monkeypatch):
    monkeypatch.setenv("AALI_STUDIO_UPDATE_PUBKEY", "")
    monkeypatch.setenv("AALI_UPDATE_PUBLIC_KEY", "")
    monkeypatch.setattr(studio_version, "UPDATE_PUBKEY_HEX", "")
    monkeypatch.setattr(studio_version, "_KEY_FILE", Path("D:/nope/none.txt"))
    info = su.status()
    assert info["has_public_key"] is False
    assert info["can_update"] is False
    html = update_ui.status_bar_html(info)
    assert "✓" not in html
    assert "—" in html          # 'not checked', not 'checked and current'


def test_a_checked_up_to_date_build_shows_a_tick():
    su._UPDATE.update({"checked": True, "update_available": False})
    assert "✓" in update_ui.status_bar_html(su.status())


# ————— [2] the banner —————
def test_the_banner_appears_when_an_update_is_available():
    su._UPDATE.update({"checked": True, "update_available": True,
                       "auto_apply_allowed": True, "latest_version": "1.1.0"})
    html = update_ui.banner_html(su.status())
    assert "إصدار جديد v1.1.0 متوفر" in html
    assert "تحديث الآن" in html
    assert "لاحقاً" in html
    assert 'id="btn-update-now"' in html


def test_there_is_no_banner_when_there_is_no_update():
    su._UPDATE.update({"checked": True, "update_available": False})
    assert update_ui.banner_html(su.status()) == ""


def test_the_banner_says_when_an_update_needs_a_manual_upgrade():
    su._UPDATE.update({"checked": True, "update_available": True,
                       "auto_apply_allowed": False, "latest_version": "2.0.0",
                       "notice": su.MSG["manual_only"]})
    html = update_ui.banner_html(su.status())
    assert "ترقية يدوية" in html
    assert 'id="btn-update-now" class="primary" disabled' in html


def test_a_hub_cannot_inject_markup_into_the_banner():
    """The version and the notes are remote data — escaped in one place."""
    su._UPDATE.update({"checked": True, "update_available": True,
                       "auto_apply_allowed": True,
                       "latest_version": '1.1.0"><script>alert(1)</script>',
                       "release_notes_ar": "<img src=x onerror=alert(2)>"})
    html = update_ui.banner_html(su.status())
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


# ————— [3] the Settings → «التحديثات» section —————
def test_the_settings_section_has_everything_the_owner_asked_for():
    html = update_ui.settings_section_html(su.status())
    assert "التحديثات" in html
    assert studio_version.version_string() in html        # current version
    assert 'id="btn-update-check"' in html                # check button
    assert 'id="update-auto"' in html                     # auto-check toggle
    assert 'id="update-channel"' in html                  # stable / beta
    assert 'id="update-hub"' in html                      # editable hub URL
    assert "aali.dpdns.org" in html                       # the default hub
    assert 'id="btn-update-rollback"' in html


def test_auto_check_defaults_to_on_and_the_channel_to_stable():
    config = su.load_config()
    assert config["auto_check"] is True
    assert config["channel"] == "stable"
    assert config["hub_url"] == "aali.dpdns.org"
    html = update_ui.settings_section_html(su.status())
    assert 'id="update-auto" type="checkbox" checked' in html
    assert '<option value="beta">' in html


def test_the_config_file_is_the_one_the_owner_specified(monkeypatch):
    path = su.config_file()
    assert path.name == "config.json"
    su.save_config({"channel": "beta", "auto_check": False})
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["channel"] == "beta"
    assert reloaded["auto_check"] is False
    # the REAL location (%APPDATA%/AaliStudio on Windows, Application Support
    # on a Mac) comes from hwk_paths, not from a hardcoded path. Checked LAST:
    # delenv-ing first would let the save above write to the owner's real
    # AppData — which is exactly what an early version of this test did.
    monkeypatch.delenv("AALI_STUDIO_CONFIG_DIR", raising=False)
    assert su.config_file().parent.name == "AaliStudio"
    assert su.config_file().is_absolute()


def test_no_test_can_write_to_the_real_appdata(tmp_path):
    """The autouse fixture redirects the config dir; this pins that it does."""
    assert str(su.config_file()).startswith(str(tmp_path))


def test_the_config_refuses_nonsense():
    with pytest.raises(su.StudioUpdateError):
        su.save_config({"channel": "nightly"})
    with pytest.raises(su.StudioUpdateError):
        su.save_config({"unknown": 1})
    # a plain-http hub is refused AT SAVE TIME, not three clicks later
    with pytest.raises(shared.UpdateError):
        su.save_config({"hub_url": "http://updates.example.com"})
    assert su.load_config()["hub_url"] != "http://updates.example.com"


def test_the_hub_url_can_be_edited():
    su.save_config({"hub_url": "https://updates.example.com"})
    assert 'value="https://updates.example.com"' in \
        update_ui.settings_section_html(su.status())


# ————— [4] the post-download dialog —————
def test_the_downloaded_dialog_says_what_the_owner_asked():
    su._JOB.update({"phase": "staged", "version": "1.1.0"})
    html = update_ui.downloaded_dialog_html(su.status())
    assert "تم تنزيل التحديث" in html
    assert "إعادة التشغيل الآن" in html
    assert "لاحقاً" in html
    assert 'id="btn-update-restart"' in html
    assert 'id="update-done-dialog"' in html


# ————— the check button really checks (real hub, real signature) —————
def test_the_check_button_triggers_a_real_check(client, hub):
    with client:
        response = client.post("/api/update/check", json={"force": True})
        data = response.get_json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["update_available"] is True
    assert data["latest_version"] == "1.1.0"
    assert data["auto_apply_allowed"] is True
    assert su._UPDATE["update_available"] is True
    assert "إصدار جديد v1.1.0 متوفر" in \
        update_ui.banner_html(su.status())


def test_a_failing_check_is_reported_not_swallowed(client, monkeypatch):
    def boom(*_a, **_k):
        raise shared.UpdateError("manifest signature invalid")
    monkeypatch.setattr(shared, "check_for_update", boom)
    with client:
        response = client.post("/api/update/check", json={"force": True})
        data = response.get_json()
    assert data["ok"] is False
    assert "manifest signature invalid" in data["error"]
    assert "فشل التحديث" in data["error"]


def test_an_unkeyed_build_refuses_instead_of_pretending(client, monkeypatch):
    monkeypatch.setenv("AALI_STUDIO_UPDATE_PUBKEY", "")
    monkeypatch.setenv("AALI_UPDATE_PUBLIC_KEY", "")
    monkeypatch.setattr(studio_version, "UPDATE_PUBKEY_HEX", "")
    monkeypatch.setattr(studio_version, "_KEY_FILE", Path("D:/nope/none.txt"))
    with client:
        data = client.post("/api/update/check", json={"force": True}).get_json()
    assert data["ok"] is False
    assert data["update_available"] is False
    assert "مفتاح تحقق" in data["error"]


# ————— the apply button really applies (download -> stage -> activate) —————
def test_the_apply_button_downloads_verifies_and_activates(client, hub,
                                                           monkeypatch):
    _url, artifact = hub
    launched: list[str] = []
    monkeypatch.setattr(su, "launch_payload",
                        lambda directory: launched.append(str(directory)))
    monkeypatch.setenv("AALI_STUDIO_UPDATE_NO_EXIT", "1")
    installed_100()          # a REAL 1.0.0 install to replace
    with client:
        started = client.post("/api/update/download").get_json()
        assert started["ok"] is True
        # the download runs on a worker thread — wait for it honestly
        for _ in range(100):
            phase = su.job_status()["phase"]
            if phase in ("staged", "error"):
                break
            threading.Event().wait(0.05)
        assert su.job_status()["phase"] == "staged", su.job_status()
        staged = Path(su.job_status()["staged"])
        assert (staged / su.payload_name()).is_file()

        applied = client.post("/api/update/activate").get_json()
    assert applied["ok"] is True
    assert launched, "the new payload must actually be started"
    current = Path(hub_install_dir()) / shared.CURRENT_DIR
    assert (current / su.payload_name()).is_file()
    assert (current / "VERSION").read_text().strip() == "1.1.0"
    assert (Path(hub_install_dir()) / "previous" / "1.0.0" / "VERSION").is_file()


def hub_install_dir() -> str:
    return os.environ["AALI_STUDIO_UPDATE_DIR"]


def installed_100() -> Path:
    """Put a real 1.0.0 payload in current/ — what an installed Studio has."""
    current = Path(hub_install_dir()) / shared.CURRENT_DIR
    current.mkdir(parents=True, exist_ok=True)
    (current / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (current / su.payload_name()).write_text("old-binary", encoding="utf-8")
    return current


def test_the_exit_after_activation_has_a_kill_switch(monkeypatch):
    """The activate route ends the process — on a timer, and only on request.

    Found the hard way: the first version of this route called os._exit(0)
    inline, which silently took the whole pytest runner down (exit code 0, one
    dot printed). Any run that must survive an activation sets
    AALI_STUDIO_UPDATE_NO_EXIT=1 — the suite, and any automation.
    """
    monkeypatch.delenv("AALI_STUDIO_UPDATE_NO_EXIT", raising=False)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code=0: exits.append(code))
    assert su.schedule_exit(delay=0.01) is True
    for _ in range(100):
        if exits:
            break
        threading.Event().wait(0.01)
    assert exits == [0], "the exit must still happen on a timer, not inline"

    monkeypatch.setenv("AALI_STUDIO_UPDATE_NO_EXIT", "1")
    exits.clear()
    assert su.schedule_exit(delay=0.01) is False
    threading.Event().wait(0.05)
    assert exits == []


def test_activate_refuses_when_nothing_was_downloaded(client, monkeypatch):
    monkeypatch.setenv("AALI_STUDIO_UPDATE_NO_EXIT", "1")
    monkeypatch.setattr(su, "check", lambda **_k: {"ok": True,
                                                   "update_available": False})
    with client:
        data = client.post("/api/update/activate").get_json()
    assert data["ok"] is False


def test_two_failed_launches_roll_the_install_back(client, hub, monkeypatch):
    """The Studio side of the rollback contract, through su.startup()."""
    _url, _artifact = hub
    launched: list[str] = []
    monkeypatch.setattr(su, "launch_payload",
                        lambda directory: launched.append(str(directory)))
    monkeypatch.setenv("AALI_STUDIO_UPDATE_NO_EXIT", "1")
    installed_100()
    with client:
        client.post("/api/update/download")
        for _ in range(100):
            if su.job_status()["phase"] in ("staged", "error"):
                break
            threading.Event().wait(0.05)
        client.post("/api/update/activate")

    current = Path(hub_install_dir()) / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.1.0"
    assert su.startup()["status"] == "failed-launch"
    assert su.startup()["status"] == "rolled-back"
    assert (current / "VERSION").read_text().strip() == "1.0.0"
    assert su.status()["previous_version"] == ""


def test_a_healthy_launch_accepts_the_new_payload(client, hub, monkeypatch):
    _url, _artifact = hub
    monkeypatch.setattr(su, "launch_payload", lambda directory: None)
    monkeypatch.setenv("AALI_STUDIO_UPDATE_NO_EXIT", "1")
    installed_100()
    with client:
        client.post("/api/update/download")
        for _ in range(100):
            if su.job_status()["phase"] in ("staged", "error"):
                break
            threading.Event().wait(0.05)
        client.post("/api/update/activate")
    su.startup()
    su.launch_ok()
    assert su.startup()["status"] == "clean"


# ————— the rollback button —————
def test_the_rollback_button_restores_the_previous_payload(client, hub,
                                                           monkeypatch):
    _url, _artifact = hub
    monkeypatch.setattr(su, "launch_payload", lambda directory: None)
    monkeypatch.setenv("AALI_STUDIO_UPDATE_NO_EXIT", "1")
    installed_100()
    with client:
        client.post("/api/update/download")
        for _ in range(100):
            if su.job_status()["phase"] in ("staged", "error"):
                break
            threading.Event().wait(0.05)
        client.post("/api/update/activate")
        data = client.post("/api/update/rollback").get_json()
    assert data["ok"] is True
    assert "استُعيد" in data["message"]
    current = Path(hub_install_dir()) / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.0.0"


# ————— routes, UI fragments and the honest limits —————
def test_every_update_route_exists(client):
    with client:
        rules = {rule.rule for rule in client.application.url_map.iter_rules()}
    for rule in ("/api/update/status", "/api/update/check",
                 "/api/update/download", "/api/update/activate",
                 "/api/update/rollback", "/api/update/config"):
        assert rule in rules, rule
    assert "/api/update/ui/<part>" in rules


def test_the_status_route_reports_everything_the_client_renders():
    app = studio_server.create_app()
    with app.test_client() as test_client:
        data = test_client.get("/api/update/status").get_json()
    for key in ("version", "version_string", "build_date", "commit",
                "platform", "config", "can_update", "has_public_key",
                "update_available", "auto_apply_allowed", "latest_version",
                "job", "log_path", "messages"):
        assert key in data, key


def test_the_ui_fragments_are_served_and_escaped():
    app = studio_server.create_app()
    with app.test_client() as test_client:
        bar = test_client.get("/api/update/ui/bar")
        settings = test_client.get("/api/update/ui/settings")
        dialog = test_client.get("/api/update/ui/dialog")
        script = test_client.get("/api/update/ui/script")
        missing = test_client.get("/api/update/ui/nope")
    assert bar.status_code == 200
    assert "update-status" in bar.get_data(as_text=True)
    assert "التحديثات" in settings.get_data(as_text=True)
    assert "تم تنزيل التحديث" in dialog.get_data(as_text=True)
    assert "updateActivate" in script.get_data(as_text=True)
    assert missing.status_code == 404


def test_a_source_run_says_it_cannot_update_itself(monkeypatch):
    monkeypatch.delenv("AALI_STUDIO_UPDATE_DIR", raising=False)
    monkeypatch.setattr(su, "is_frozen", lambda: False)
    ok, reason = su.can_update()
    assert ok is False
    assert "نسخة تُشغَّل من المصدر" in reason
    assert su.check()["ok"] is False


def test_the_update_kill_switch_is_honoured(monkeypatch):
    monkeypatch.setenv("AALI_STUDIO_UPDATE_OFF", "1")
    ok, reason = su.can_update()
    assert ok is False
    assert "معطّلة" in reason


def test_auto_check_honours_the_owner_toggle(hub, monkeypatch):
    su.save_config({"auto_check": False})
    calls: list[bool] = []
    monkeypatch.setattr(su, "check", lambda force=False: calls.append(force)
                        or {"ok": True, "update_available": False})
    assert su.auto_check() is None
    assert calls == []
    su.save_config({"auto_check": True})
    su.auto_check()
    assert calls == [False]


def test_the_payload_name_is_platform_correct():
    expected = {"win": "Aali-Studio.exe", "macos": "Aali-Studio.app"}.get(
        shared.host_platform(), "Aali-Studio")
    assert su.payload_name() == expected


def test_the_client_wiring_lives_on_the_server_not_in_a_second_js_copy():
    """One source of truth for the update copy — no drift in app.js."""
    app_js = (REPO / "build-desktop" / "aali-studio" / "web" /
              "app.js").read_text(encoding="utf-8")
    for arabic in ("إصدار جديد", "تحديث الآن", "إعادة التشغيل الآن",
                   "أنت على أحدث إصدار"):
        assert arabic not in app_js, arabic
    index = (REPO / "build-desktop" / "aali-studio" / "web" /
             "index.html").read_text(encoding="utf-8")
    for slot in ("update-banner-slot", "update-status-slot",
                 "update-settings-slot", "update-dialog-slot"):
        assert slot in index, slot


def test_the_about_box_and_health_report_the_same_version(client):
    with client:
        about = client.get("/api/about").get_json()
        health = client.get("/api/health").get_json()
    assert about["version"] == health["version"] == studio_version.__version__
    assert about["build_date"] == studio_version.BUILD_DATE