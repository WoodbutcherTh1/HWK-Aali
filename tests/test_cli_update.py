"""tests/test_cli_update.py — آلي CLI's /update, on the same contract as Studio
and Desktop.

Two things this file takes seriously:

* **the CLI never touches the real network in a test** — every check goes to a
  real loopback hub publishing a real Ed25519 manifest, or is turned off at
  ``can_update()``. A test that reached ``aali.dpdns.org`` would be slow,
  flaky, and would quietly depend on the owner's deployment.
* **bilingual copy is asserted, not assumed** — every message has an Arabic and
  an English twin, and ``lang`` (ar | en | both) decides which one is printed.
  A missing English string is a bug an Arabic-only test would never catch.
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
for _extra in ("file-agent", "scripts"):
    if (REPO / _extra).is_dir() and str(REPO / _extra) not in sys.path:
        sys.path.insert(0, str(REPO / _extra))

import cli_update as cu  # noqa: E402
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
    monkeypatch.setenv("AALI_CLI_UPDATE_CONFIG", str(tmp_path / "cli.json"))
    monkeypatch.setenv("AALI_CLI_UPDATE_DIR", str(tmp_path / "install"))
    monkeypatch.setenv("AALI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AALI_CLI_UPDATE_PUBKEY", PUB_HEX)
    monkeypatch.setenv("AALI_CLI_UPDATE_NO_EXIT", "1")
    monkeypatch.delenv("AALI_CLI_UPDATE_OFF", raising=False)
    monkeypatch.delenv("AALI_UPDATE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("AALI_UPDATE_TOKEN", raising=False)
    cu.reset_state()
    yield
    cu.reset_state()


class Recorder:
    """The CLI's `say(tag, text, style)` — capture instead of painting."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []

    def __call__(self, tag: str, text: str, style: str) -> None:
        self.lines.append((tag, text, style))

    @property
    def text(self) -> str:
        return "\n".join(t for _tag, t, _style in self.lines)


@pytest.fixture
def say():
    return Recorder()


# ————— a real signed hub —————
def make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def sign_hex(message: str) -> str:
    return PRIV.sign(message.encode("utf-8")).hex()


class _Handler(BaseHTTPRequestHandler):
    artifact = b""
    manifest: dict = {}

    def do_GET(self):  # noqa: N802
        if "/download/" in self.path:
            body, kind = self.artifact, "application/zip"
        elif self.path.startswith("/updates/cli/latest"):
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
    from aali_hub.update_server import build_manifest, sign_manifest
    platform = shared.host_platform()
    artifact = make_zip({cu.payload_name(): "new-cli", "VERSION": "1.1.0"})
    digest = hashlib.sha256(artifact).hexdigest()
    manifest = build_manifest(
        "1.1.0", "1.0.0", f"/updates/cli/1.1.0/download/{platform}", digest,
        release_notes_ar="إصدار جديد للطرفية", release_notes_en="new CLI build")
    manifest["app"] = "cli"
    manifest["platform"] = platform
    manifest["artifact_signature"] = sign_hex(digest)
    _Handler.artifact = artifact
    _Handler.manifest = sign_manifest(manifest, PRIV)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    cu.save_config({"hub_url": url})
    try:
        yield url, artifact
    finally:
        server.shutdown()
        server.server_close()


def installed() -> Path:
    current = Path(os.environ["AALI_CLI_UPDATE_DIR"]) / shared.CURRENT_DIR
    current.mkdir(parents=True, exist_ok=True)
    (current / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (current / cu.payload_name()).write_text("old-cli", encoding="utf-8")
    return current


# ————— configuration —————
def test_the_config_file_is_where_the_owner_asked(tmp_path, monkeypatch):
    monkeypatch.delenv("AALI_CLI_UPDATE_CONFIG", raising=False)
    path = cu.config_file()
    assert path.name == "cli_update.json"
    assert path.parent.name == ".aali"
    assert path.parent.parent == Path.home()
    # the override exists so a test (or a second profile) can move it
    monkeypatch.setenv("AALI_CLI_UPDATE_CONFIG", str(tmp_path / "x.json"))
    assert cu.config_file() == tmp_path / "x.json"


def test_the_defaults_are_the_ones_the_owner_specified():
    config = cu.load_config()
    assert config["auto_check"] is True
    assert config["channel"] == "stable"
    assert config["hub_url"] == "aali.dpdns.org"
    assert config["lang"] == "ar"


def test_the_config_refuses_nonsense():
    for bad in ({"channel": "nightly"}, {"lang": "fr"}, {"nope": 1}):
        with pytest.raises(cu.CliUpdateError):
            cu.save_config(bad)
    with pytest.raises(shared.UpdateError):
        cu.save_config({"hub_url": "http://updates.example.com"})


def test_the_config_survives_a_round_trip():
    cu.save_config({"channel": "beta", "lang": "both", "auto_check": False})
    saved = json.loads(cu.config_file().read_text(encoding="utf-8"))
    assert saved == {"auto_check": False, "channel": "beta",
                     "hub_url": "aali.dpdns.org", "lang": "both"}
    assert cu.load_config()["lang"] == "both"


# ————— bilingual copy —————
def test_every_message_has_an_arabic_and_an_english_twin():
    fields = {"version": "9.9.9", "error": "E", "path": "P", "reason": "R",
              "value": "V", "channel": "C"}
    # Command SYNTAX is the same in both languages on purpose; only prose has
    # to differ. Naming the exception beats weakening the assertion.
    same_in_both = {"usage"}
    for key, pair in cu.MSG.items():
        assert isinstance(pair, tuple) and len(pair) == 2, key
        assert pair[0].strip() and pair[1].strip(), key
        if key not in same_in_both:
            assert pair[0] != pair[1], f"{key}: the two languages are identical"
        # both twins must accept the SAME fields — a message that formats in
        # Arabic and blows up in English is a real bug
        for text in pair:
            rendered = text.format(**fields)
            assert "{" not in rendered, f"{key}: unformatted field in {text!r}"


def test_the_language_choice_picks_the_string():
    assert cu.t("up_to_date", "ar") == "أنت على أحدث إصدار."
    assert cu.t("up_to_date", "en") == "You are on the latest version."
    arabic, english = cu.both("available", version="1.1.0")
    assert "1.1.0" in arabic and "1.1.0" in english
    assert arabic != english
    assert "update" not in arabic.lower() or "apply" in arabic


def test_an_unknown_message_key_is_a_loud_error():
    with pytest.raises(cu.CliUpdateError):
        cu.t("no-such-message")


def test_lang_both_prints_both_languages(say):
    cu.save_config({"lang": "both"})
    cu.run_command("version", say=say)
    assert any("الإصدار الحالي" in text for _t, text, _s in say.lines)
    assert any("Current version" in text for _t, text, _s in say.lines)


def test_lang_en_prints_only_english(say):
    cu.save_config({"lang": "en"})
    cu.run_command("version", say=say)
    assert "Current version" in say.text
    assert "الإصدار الحالي" not in say.text


# ————— /update version + status —————
def test_update_version_shows_current_and_latest(say, hub):
    cu.run_command("check", say=say)
    say.lines.clear()
    lines = cu.run_command("version", say=say)
    assert any(cu.CLI_VERSION in line for line in lines)
    assert any("1.1.0" in line for line in lines)


def test_update_version_says_when_nothing_is_published(say, monkeypatch):
    monkeypatch.setattr(shared, "check_for_update",
                        lambda *_a, **_k: _offline_manifest())
    cu.run_command("check", say=say)
    say.lines.clear()
    lines = cu.run_command("version", say=say)
    assert "لا يوجد إصدار منشور بعد." in lines


def _offline_manifest(**over):
    payload = {
        "update_available": False, "auto_apply_allowed": False,
        "latest_version": "", "min_version": "", "release_notes_ar": "",
        "release_notes_en": "", "url": "", "sha256": "",
        "artifact_signature": "", "ok": True, "checked": True,
    }
    payload.update(over)
    return payload


def test_update_with_no_verb_prints_the_status(say):
    lines = cu.run_command("", say=say)
    joined = "\n".join(lines)
    assert cu.CLI_VERSION in joined
    assert "مفتاح التحقق" in joined
    assert cu.config_file().name in joined


# ————— /update check —————
def test_update_check_reports_the_release(say, hub):
    lines = cu.run_command("check", say=say)
    joined = "\n".join(lines)
    assert "1.1.0" in joined
    assert "/update apply" in joined          # the owner's exact launch copy
    assert "إصدار جديد للطرفية" in joined      # arabic release notes


def test_update_check_says_up_to_date_when_there_is_nothing(say, monkeypatch):
    monkeypatch.setattr(shared, "check_for_update",
                        lambda *_a, **_k: _offline_manifest())
    lines = cu.run_command("check", say=say)
    assert lines == ["up-to-date"]


def test_a_failing_check_is_reported_both_ways(say, monkeypatch):
    def boom(*_a, **_k):
        raise shared.UpdateError("manifest signature invalid")
    monkeypatch.setattr(shared, "check_for_update", boom)
    cu.save_config({"lang": "both"})
    cu.run_command("check", say=say)
    assert "فشل التحديث" in say.text
    assert "Update failed" in say.text


# ————— /update auto + channel —————
def test_update_auto_toggles_and_persists(say):
    assert cu.run_command("auto off", say=say) == ["off"]
    assert cu.load_config()["auto_check"] is False
    assert cu.run_command("auto on", say=say) == ["on"]
    assert cu.load_config()["auto_check"] is True
    assert "مفعّل" in say.text


def test_update_auto_refuses_nonsense(say):
    assert cu.run_command("auto maybe", say=say) == []
    assert "قيمة غير معروفة" in say.text
    assert cu.load_config()["auto_check"] is True     # unchanged


def test_update_channel_switches_and_says_so(say):
    assert cu.run_command("channel beta", say=say) == ["beta"]
    assert cu.load_config()["channel"] == "beta"
    assert "تجريبي" in say.text
    assert cu.run_command("channel stable", say=say) == ["stable"]
    assert cu.run_command("channel nightly", say=say) == []


def test_an_unknown_verb_prints_the_usage(say):
    cu.run_command("teleport", say=say)
    assert "/update check" in say.text


# ————— /update apply: the real flow —————
def test_apply_verifies_stages_and_activates(say, hub, monkeypatch):
    installed()
    launched: list[str] = []
    monkeypatch.setattr(cu, "launch_payload",
                        lambda directory: launched.append(str(directory)))
    lines = cu.run_command("apply", say=say)
    assert "تم التنزيل والتحقق" in say.text
    assert lines and lines[0] == "staged"
    assert launched and launched[0].endswith(shared.CURRENT_DIR)
    target = Path(os.environ["AALI_CLI_UPDATE_DIR"])
    current = target / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.1.0"
    assert (target / "previous" / "1.0.0" / cu.payload_name()).is_file()


def test_apply_asks_before_it_relaunches_the_terminal(say, hub, monkeypatch):
    """A terminal client must never respawn itself behind your back."""
    installed()
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code=0: exits.append(code))
    monkeypatch.setenv("AALI_CLI_UPDATE_NO_EXIT", "")
    monkeypatch.setattr(cu, "launch_payload", lambda directory: None)
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    cu.run_command("apply", say=say, confirm=confirm)
    assert asked, "apply must ask before restarting"
    threading.Event().wait(0.1)
    assert exits == [], "answering no must not exit"

    def confirm_yes(prompt: str) -> str:
        return "y"

    cu.run_command("apply", say=say, confirm=confirm_yes)
    for _ in range(100):
        if exits:
            break
        threading.Event().wait(0.01)
    assert exits == [0]
    assert "إعادة التشغيل" in asked[0]


def test_apply_says_up_to_date_when_there_is_nothing(say, monkeypatch):
    monkeypatch.setattr(shared, "check_for_update",
                        lambda *_a, **_k: _offline_manifest())
    assert cu.run_command("apply", say=say) == ["up-to-date"]


def test_apply_refuses_when_nothing_was_downloaded(say, monkeypatch):
    # download_and_stage is stubbed so this test never reaches the network —
    # the point is the ERROR the command prints, not the download itself.
    monkeypatch.setattr(cu, "download_and_stage",
                        lambda *a, **k: {"ok": True, "phase": "staged",
                                         "staged": "C:/staged/1.1.0"})
    monkeypatch.setattr(cu, "activate",
                        lambda: {"ok": False, "error_key": "not_downloaded"})
    lines = cu.run_command("apply", say=say)
    assert "لم يُنزَّل التحديث بعد" in say.text
    assert lines == ["not_downloaded"]


# ————— rollback —————
def test_two_failed_launches_roll_back(hub, monkeypatch):
    installed()
    monkeypatch.setattr(cu, "launch_payload", lambda directory: None)
    say = Recorder()
    cu.run_command("apply", say=say)
    current = Path(os.environ["AALI_CLI_UPDATE_DIR"]) / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.1.0"
    assert cu.startup()["status"] == "failed-launch"
    assert cu.startup()["status"] == "rolled-back"
    assert (current / "VERSION").read_text().strip() == "1.0.0"


def test_a_healthy_launch_accepts_the_new_payload(hub, monkeypatch):
    installed()
    monkeypatch.setattr(cu, "launch_payload", lambda directory: None)
    cu.run_command("apply", say=Recorder())
    cu.startup()
    cu.launch_ok()
    assert cu.startup()["status"] == "clean"


def test_the_rollback_command_restores_and_reports(say, hub, monkeypatch):
    installed()
    monkeypatch.setattr(cu, "launch_payload", lambda directory: None)
    cu.run_command("apply", say=Recorder())
    cu.run_command("rollback", say=say)
    assert "استُعيد الإصدار السابق" in say.text
    current = Path(os.environ["AALI_CLI_UPDATE_DIR"]) / shared.CURRENT_DIR
    assert (current / "VERSION").read_text().strip() == "1.0.0"


def test_rollback_with_nothing_saved_is_honest(say):
    cu.run_command("rollback", say=say)
    assert "لا يوجد إصدار سابق محفوظ" in say.text


# ————— the launch-time notice —————
def test_the_launch_notice_says_exactly_what_the_owner_wrote(hub):
    announced: list[tuple[str, str]] = []
    result = cu.auto_check(announce=lambda pair: announced.append(pair))
    assert result["update_available"] is True
    arabic, english = announced[0]
    assert arabic == ("🎉 إصدار جديد v1.1.0 متوفر. "
                      "اكتب /update apply للتحديث.")
    assert "1.1.0" in english and "/update apply" in english


def test_the_launch_notice_stays_quiet_when_auto_check_is_off(hub):
    cu.save_config({"auto_check": False})
    announced: list[tuple[str, str]] = []
    assert cu.auto_check(announce=lambda pair: announced.append(pair)) is None
    assert announced == []


def test_the_launch_notice_stays_quiet_when_there_is_no_update(monkeypatch):
    monkeypatch.setattr(shared, "check_for_update",
                        lambda *_a, **_k: _offline_manifest())
    announced: list[tuple[str, str]] = []
    cu.auto_check(announce=lambda pair: announced.append(pair))
    assert announced == []


def test_the_background_check_never_raises(say):
    thread = cu.start_background_check(lambda pair: None)
    thread.join(timeout=10)
    assert not thread.is_alive()


# ————— the honest limits + wiring —————
def test_a_source_run_says_it_cannot_update_itself(monkeypatch):
    monkeypatch.delenv("AALI_CLI_UPDATE_DIR", raising=False)
    monkeypatch.setattr(cu, "is_frozen", lambda: False)
    ok, reason = cu.can_update()
    assert ok is False
    assert "نسخة المصدر" in reason


def test_an_unkeyed_build_refuses_instead_of_pretending(monkeypatch):
    monkeypatch.delenv("AALI_CLI_UPDATE_PUBKEY", raising=False)
    monkeypatch.delenv("AALI_UPDATE_PUBLIC_KEY", raising=False)
    assert cu.can_update() == (False, cu.t("no_key", "ar"))
    result = cu.check(force=True)
    assert result["ok"] is False and result["update_available"] is False


def test_the_kill_switch_is_honoured(monkeypatch):
    monkeypatch.setenv("AALI_CLI_UPDATE_OFF", "1")
    ok, reason = cu.can_update()
    assert ok is False and "معطّلة" in reason


def test_the_exit_has_a_kill_switch(monkeypatch):
    monkeypatch.delenv("AALI_CLI_UPDATE_NO_EXIT", raising=False)
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code=0: exits.append(code))
    assert cu.schedule_exit(delay=0.01) is True
    for _ in range(100):
        if exits:
            break
        threading.Event().wait(0.01)
    assert exits == [0]
    monkeypatch.setenv("AALI_CLI_UPDATE_NO_EXIT", "1")
    exits.clear()
    assert cu.schedule_exit(delay=0.01) is False
    threading.Event().wait(0.05)
    assert exits == []


def test_the_payload_name_matches_the_built_exe():
    expected = "aali-cli.exe" if shared.host_platform() == "win" else "aali-cli"
    assert cu.payload_name() == expected


def test_the_cli_wires_update_into_its_help_and_completion():
    source = (REPO / "scripts" / "aali_cli.py").read_text(encoding="utf-8")
    assert '"/update"' in source
    assert "cli_update.run_command" in source
    assert "cli_update.start_background_check" in source
    assert "cli_update.startup()" in source
    assert "/update" in cu.MSG["usage"][0]


def test_the_build_scripts_can_see_the_shared_updater():
    """A frozen exe that cannot import shared.updater has no /update at all."""
    bat = (REPO / "scripts" / "build_desktop.bat").read_text(encoding="ascii")
    assert "--hidden-import shared.updater" in bat
    sh = (REPO / "scripts" / "build_all_macos.sh").read_text(encoding="utf-8")
    assert "--hidden-import shared.updater" in sh
    assert "--paths scripts" in sh