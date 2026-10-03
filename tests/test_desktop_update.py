"""tests/test_desktop_update.py — آلي Desktop's self-update.

Desktop has no HTML template: its UI is JavaScript injected into Aali's own web
UI, so every Arabic string and every id it renders is BUILT IN PYTHON
(``desktop_update``) and asserted here. A test that only checked "the JS exists"
would pass while the copy drifted, so the fragments are pinned as text.

The end-to-end path runs against a REAL loopback hub with a REAL Ed25519
manifest, through the REAL pywebview Bridge methods (JSON in, JSON out), because
pywebview marshals plain values only and a dict crossing that boundary is a
class of bug this app has hit before.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for _extra in ("file-agent", "scripts", "."):
    if (REPO / _extra).is_dir() and str(REPO / _extra) not in sys.path:
        sys.path.insert(0, str(REPO / _extra))

import aali_desktop_app  # noqa: E402
import desktop_update as du  # noqa: E402
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
    monkeypatch.setenv("AALI_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_DIR", str(tmp_path / "install"))
    monkeypatch.setenv("AALI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_PUBKEY", PUB_HEX)
    monkeypatch.delenv("AALI_DESKTOP_UPDATE_OFF", raising=False)
    monkeypatch.delenv("AALI_UPDATE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("AALI_UPDATE_TOKEN", raising=False)
    # NEVER let an activate test take the runner down with os._exit(0) — that
    # exact bug cost an hour in the Studio suite. One kill-switch test below
    # deliberately removes it.
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_NO_EXIT", "1")
    du.reset_state()
    yield
    du.reset_state()


# ————— a real signed hub —————
def make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def sign_hex(message: str) -> str:
    return PRIV.sign(message.encode("utf-8")).hex()


class _Handler:
    artifact = b""
    manifest: dict = {}


try:  # pragma: no cover - import shape only
    from http.server import BaseHTTPRequestHandler as _Base
except ImportError:  # pragma: no cover
    _Base = object


class _Server(_Base):  # type: ignore[misc,valid-type]
    def do_GET(self):  # noqa: N802
        if "/download/" in self.path:
            body, kind = _Handler.artifact, "application/zip"
        elif self.path.startswith("/updates/desktop/latest"):
            body = json.dumps({"ok": True, "update": _Handler.manifest}).encode()
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
    from aali_hub.update_server import build_manifest, sign_manifest
    platform = shared.host_platform()
    artifact = make_zip({du.payload_name(): "new-binary", "VERSION": "1.1.0"})
    digest = hashlib.sha256(artifact).hexdigest()
    manifest = build_manifest(
        "1.1.0", "1.0.0", f"/updates/desktop/1.1.0/download/{platform}",
        digest, release_notes_ar="تحسينات", release_notes_en="improvements")
    manifest["app"] = "desktop"
    manifest["platform"] = platform
    manifest["artifact_signature"] = sign_hex(digest)
    _Handler.artifact = artifact
    _Handler.manifest = sign_manifest(manifest, PRIV)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    du.save_config({"hub_url": url})
    try:
        yield url, artifact
    finally:
        server.shutdown()
        server.server_close()


def installed() -> Path:
    current = Path(os.environ["AALI_DESKTOP_UPDATE_DIR"]) / shared.CURRENT_DIR
    current.mkdir(parents=True, exist_ok=True)
    (current / "VERSION").write_text("1.0.3\n", encoding="utf-8")
    (current / du.payload_name()).write_text("old-binary", encoding="utf-8")
    return current


def wait_for_phase(*phases: str, tries: int = 120) -> str:
    for _ in range(tries):
        phase = du.job_status()["phase"]
        if phase in phases:
            return phase
        threading.Event().wait(0.05)
    return du.job_status()["phase"]


# ————— [1] the version chip —————
def test_the_chip_shows_the_running_version():
    chip = du.status_chip_html(du.status())
    assert f'v{du.app_version()}' in chip
    assert 'id="aaliv"' in chip
    assert du.app_version() == aali_desktop_app.APP_VERSION


def test_the_chip_never_shows_a_tick_it_cannot_back(monkeypatch):
    monkeypatch.delenv("AALI_DESKTOP_UPDATE_PUBKEY", raising=False)
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_PUBKEY_FILE",
                       str(Path("D:/nope/none.txt")))
    monkeypatch.setattr(du.hwk_paths, "data_root",
                        lambda: Path("D:/nope"))
    info = du.status()
    assert info["can_update"] is False
    chip = du.status_chip_html(info)
    assert "✓" not in chip and "—" in chip


def test_a_checked_current_build_shows_a_tick():
    du._UPDATE.update({"checked": True, "update_available": False})
    assert "✓" in du.status_chip_html(du.status())


# ————— [2] the banner —————
def test_the_banner_appears_only_when_something_is_new():
    assert du.banner_html(du.status()) == ""
    du._UPDATE.update({"checked": True, "update_available": True,
                       "auto_apply_allowed": True, "latest_version": "1.1.0",
                       "release_notes_ar": "تحسينات"})
    banner = du.banner_html(du.status())
    assert "إصدار جديد v1.1.0 متوفر" in banner
    assert "تحديث الآن" in banner and "لاحقاً" in banner
    assert "تحسينات" in banner


def test_the_banner_disables_apply_below_the_minimum():
    du._UPDATE.update({"update_available": True, "auto_apply_allowed": False,
                       "latest_version": "2.0.0", "notice": du.MSG["manual_only"]})
    assert "disabled" in du.banner_html(du.status())


def test_hub_supplied_text_is_escaped():
    du._UPDATE.update({"update_available": True, "auto_apply_allowed": True,
                       "latest_version": "1.1.0\"><script>alert(1)</script>",
                       "release_notes_ar": "<img src=x onerror=alert(2)>"})
    banner = du.banner_html(du.status())
    assert "<script>" not in banner and "<img" not in banner
    assert "&lt;script&gt;" in banner and "&lt;img" in banner


# ————— [3] the settings box —————
def test_the_settings_box_has_every_control():
    box = du.settings_html(du.status())
    assert "التحديثات" in box
    assert du.app_version() in box
    assert 'id="aalivcheck"' in box
    assert 'id="aalivauto" type="checkbox" checked' in box
    assert 'id="aalivchan"' in box
    assert 'id="aalivhub"' in box
    assert "aali.dpdns.org" in box


def test_the_config_file_is_the_one_the_owner_specified(tmp_path,
                                                        monkeypatch):
    path = du.config_file()
    assert path.name == "config.json"
    assert str(path).startswith(str(tmp_path))
    # the REAL location comes from hwk_paths, not a hardcoded path. Checked
    # AFTER the sandbox assertion — reading it first would let a later save in
    # this module land on the owner's real AppData.
    monkeypatch.delenv("AALI_CONFIG_DIR", raising=False)
    assert du.config_file().parent.name == "AaliDesktop"


def test_the_config_refuses_nonsense():
    with pytest.raises(du.DesktopUpdateError):
        du.save_config({"channel": "nightly"})
    with pytest.raises(du.DesktopUpdateError):
        du.save_config({"nope": 1})
    with pytest.raises(shared.UpdateError):
        du.save_config({"hub_url": "http://updates.example.com"})


# ————— [4] the downloaded dialog —————
def test_the_downloaded_dialog_says_what_the_owner_asked():
    du._JOB.update({"phase": "staged", "version": "1.1.0"})
    dialog = du.done_dialog_html(du.status())
    assert "تم تنزيل التحديث" in dialog
    assert "إعادة التشغيل الآن" in dialog
    assert "لاحقاً" in dialog
    assert 'id="aalidonerestart"' in dialog


# ————— the real end-to-end, through the pywebview Bridge —————
def test_the_bridge_round_trips_json_strings(hub):
    bridge = aali_desktop_app.Bridge()
    for name in ("update_status", "update_ui"):
        payload = getattr(bridge, name)()
        assert isinstance(payload, str), name
        assert isinstance(json.loads(payload), dict), name


def test_check_through_the_bridge_finds_the_release(hub):
    bridge = aali_desktop_app.Bridge()
    data = json.loads(bridge.update_check(True))
    assert data["ok"] is True
    assert data["update_available"] is True
    assert data["latest_version"] == "1.1.0"


def test_download_verify_activate_and_restart(hub, monkeypatch):
    installed()
    launched: list[str] = []
    monkeypatch.setattr(du, "launch_payload",
                        lambda directory: launched.append(str(directory)))
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_NO_EXIT", "1")
    bridge = aali_desktop_app.Bridge()
    started = json.loads(bridge.update_download())
    assert started["ok"] is True
    assert wait_for_phase("staged", "error") == "staged", du.job_status()

    target = Path(os.environ["AALI_DESKTOP_UPDATE_DIR"])
    staged = Path(du.job_status()["staged"])
    assert (staged / du.payload_name()).is_file()

    result = json.loads(bridge.update_activate())
    assert result["ok"] is True
    assert launched and launched[0].endswith(shared.CURRENT_DIR)
    current = target / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.1.0"
    assert (target / "previous" / "1.0.3" / du.payload_name()).is_file()


def test_two_failed_launches_roll_back(hub, monkeypatch):
    installed()
    monkeypatch.setattr(du, "launch_payload", lambda directory: None)
    bridge = aali_desktop_app.Bridge()
    bridge.update_download()
    assert wait_for_phase("staged", "error") == "staged"
    bridge.update_activate()
    current = Path(os.environ["AALI_DESKTOP_UPDATE_DIR"]) / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.1.0"
    assert du.startup()["status"] == "failed-launch"
    assert du.startup()["status"] == "rolled-back"
    assert (current / "VERSION").read_text().strip() == "1.0.3"


def test_a_healthy_launch_accepts_the_new_payload(hub, monkeypatch):
    installed()
    monkeypatch.setattr(du, "launch_payload", lambda directory: None)
    bridge = aali_desktop_app.Bridge()
    bridge.update_download()
    assert wait_for_phase("staged", "error") == "staged"
    bridge.update_activate()
    du.startup()
    du.launch_ok()
    assert du.startup()["status"] == "clean"


def test_the_rollback_button_works(hub, monkeypatch):
    installed()
    monkeypatch.setattr(du, "launch_payload", lambda directory: None)
    bridge = aali_desktop_app.Bridge()
    bridge.update_download()
    assert wait_for_phase("staged", "error") == "staged"
    bridge.update_activate()
    data = json.loads(bridge.update_rollback())
    assert data["ok"] is True
    current = Path(os.environ["AALI_DESKTOP_UPDATE_DIR"]) / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.0.3"


def test_the_exit_after_activation_has_a_kill_switch(monkeypatch):
    """The same lesson as Studio, learned there first this time."""
    monkeypatch.delenv("AALI_DESKTOP_UPDATE_NO_EXIT", raising=False)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code=0: exits.append(code))
    assert du.schedule_exit(delay=0.01) is True
    for _ in range(100):
        if exits:
            break
        threading.Event().wait(0.01)
    assert exits == [0]
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_NO_EXIT", "1")
    exits.clear()
    assert du.schedule_exit(delay=0.01) is False
    threading.Event().wait(0.05)
    assert exits == []


def test_the_rollback_button_says_when_there_is_nothing_to_restore(hub):
    bridge = aali_desktop_app.Bridge()
    data = json.loads(bridge.update_rollback())
    assert data["ok"] is False
    assert data["error"] == du.MSG["no_previous"]


def test_update_save_accepts_a_json_patch_and_refuses_bad_input(hub):
    bridge = aali_desktop_app.Bridge()
    good = json.loads(bridge.update_save(json.dumps(
        {"auto_check": False, "channel": "beta"})))
    assert good["ok"] is True
    assert good["config"]["channel"] == "beta"
    assert good["config"]["auto_check"] is False
    bad = json.loads(bridge.update_save('{"channel": "nightly"}'))
    assert bad["ok"] is False and "stable" in bad["error"]
    junk = json.loads(bridge.update_save("not json"))
    assert junk["ok"] is False


def test_a_source_run_says_it_cannot_update_itself(monkeypatch):
    monkeypatch.delenv("AALI_DESKTOP_UPDATE_DIR", raising=False)
    monkeypatch.setattr(du, "is_frozen", lambda: False)
    ok, reason = du.can_update()
    assert ok is False
    assert "نسخة تُشغَّل من المصدر" in reason


def test_the_kill_switch_is_honoured(monkeypatch):
    monkeypatch.setenv("AALI_DESKTOP_UPDATE_OFF", "1")
    ok, reason = du.can_update()
    assert ok is False and "معطّلة" in reason


def test_auto_check_honours_the_owner_toggle(hub, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(du, "check", lambda force=False: calls.append(force)
                        or {"ok": True, "update_available": False})
    du.save_config({"auto_check": False})
    assert du.auto_check() is None
    du.save_config({"auto_check": True})
    du.auto_check()
    assert calls == [False]


def test_the_payload_name_is_platform_correct():
    expected = {"win": "Aali-Desktop.exe",
                "macos": "Aali-Desktop.app"}.get(shared.host_platform(),
                                                 "Aali-Desktop")
    assert du.payload_name() == expected


def test_the_injected_js_never_builds_its_own_arabic_copy():
    """One source of truth: the copy lives in Python, the JS only wires ids."""
    forbidden = ["إصدار جديد", "تحديث الآن", "إعادة التشغيل الآن",
                 "أنت على أحدث إصدار", "تم تنزيل التحديث"]
    for text in forbidden:
        assert text not in du.UPDATE_JS, text
    # every control the JS clicks must exist in the rendered fragments, and
    # vice versa — a renamed id here is a dead button in the owner's window
    # with an update available, so the BANNER fragment (and its ids) exists
    du._UPDATE.update({"checked": True, "update_available": True,
                       "auto_apply_allowed": True, "latest_version": "1.1.0"})
    du._JOB.update({"phase": "staged", "version": "1.1.0"})
    rendered = "\n".join(du.ui_payload().values())
    for ident in ("aaliv", "aalibanner", "aalivbox", "aalidone",
                  "aalivcheck", "aalivsave", "aalibuy", "aalidonerestart",
                  "aalivauto", "aalivchan", "aalivhub", "aalidonelater",
                  "aalilater", "aalivclose"):
        assert ident in rendered, ident
    for ident in ("aaliv", "aalibanner", "aalidone", "aalivcheck",
                  "aalivsave", "aalibuy", "aalidonerestart", "aalilater",
                  "aalidonelater", "aalivclose"):
        assert ident in du.UPDATE_JS, ident


def test_the_desktop_app_still_bootstraps_its_own_sys_path():
    """Frozen builds have no PYTHONPATH — shared.updater must be reachable."""
    source = (REPO / "aali_desktop_app.py").read_text(encoding="utf-8")
    assert '_HERE / "scripts"' in source
    assert "desktop_update.UPDATE_JS" in source


def test_the_old_server_version_pill_is_still_there_and_distinct():
    """The old /api/desktop-version pill answers a DIFFERENT question."""
    source = (REPO / "aali_desktop_app.py").read_text(encoding="utf-8")
    assert "/api/desktop-version" in source
    assert "api.update_check" in du.UPDATE_JS