"""آلي CLI — the terminal client's auto-update (same contract, same code path).

Third client on :mod:`shared.updater`, so there is still ONE security story:
never trust the hub, verify SHA256 + Ed25519 client-side, refuse zip-slip,
never execute a download, keep the previous version for rollback.

What is CLI-specific:

* its own config — ``~/.aali/cli_update.json`` (``%USERPROFILE%\\.aali\\`` on
  Windows). JSON rather than the flat ``~/.aali_cli_*`` files the CLI already
  keeps, because this config is structured (hub, channel, auto-check, lang)
  and a second flat file per setting would be silly. Override with
  ``AALI_CLI_UPDATE_CONFIG``.
* its payload name — ``aali-cli.exe`` / ``aali-cli``.
* **bilingual** output. Arabic-first (this is an Arabic-first product), but
  every message has an English twin because a terminal client is exactly where
  an English-only colleague lands. ``lang`` is ``ar`` (default), ``en`` or
  ``both``; ``t()`` is the only place a message is chosen.
* ``/update apply`` does NOT silently spawn a second console: it verifies,
  stages, activates, then ASKS before relaunching. A terminal client that
  relaunches itself behind your back is a surprise, not a feature.

The commands are dispatched from ``aali_cli.repl`` through
:func:`run_command`, which takes the same ``(say, colour)`` plumbing the rest
of the CLI uses so the output looks like everything else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for _extra in (_REPO / "scripts", _REPO / "file-agent", _HERE):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from shared import updater as shared  # noqa: E402

CLI_VERSION = "1.0.0"
APP_ID = "cli"
DEFAULT_HUB = "aali.dpdns.org"
CHANNELS = ("stable", "beta")
CHANNEL_LABELS = {"stable": "مستقر · stable", "beta": "تجريبي · beta"}
LANGS = ("ar", "en", "both")
AUTO_CHECK_INTERVAL_S = 6 * 3600
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class CliUpdateError(Exception):
    """Expected, user-facing (the caller prints it in both languages)."""


#: (arabic, english). ONE place for every word the CLI says about updates.
MSG: dict[str, tuple[str, str]] = {
    "available": ("🎉 إصدار جديد v{version} متوفر. اكتب /update apply للتحديث.",
                  "🎉 A new version v{version} is available. "
                  "Type /update apply to update."),
    "up_to_date": ("أنت على أحدث إصدار.", "You are on the latest version."),
    "current": ("الإصدار الحالي: v{version}", "Current version: v{version}"),
    "latest": ("أحدث إصدار منشور: v{version}", "Latest published: v{version}"),
    "latest_none": ("لا يوجد إصدار منشور بعد.", "Nothing published yet."),
    "checking": ("جارٍ البحث عن تحديثات…", "Checking for updates…"),
    "downloading": ("جارٍ التنزيل والتحقق…", "Downloading and verifying…"),
    "downloaded": ("تم التنزيل والتحقق. جاهز للتثبيت.",
                   "Downloaded and verified. Ready to install."),
    "restart_prompt": ("إعادة التشغيل الآن؟", "Restart now?"),
    "restarted": ("أُعيد التشغيل من النسخة الجديدة.",
                  "Relaunched from the new version."),
    "manual_only": ("هذا الإصدار يحتاج ترقية يدوية.",
                    "This release needs a manual upgrade."),
    "no_key": ("لا يوجد مفتاح تحقق مثبّت — التحديثات معطّلة.",
               "No verification key is bundled — updates are disabled."),
    "not_installed": ("نسخة المصدر لا تُحدّث نفسها — التحديث يعمل على "
                      "النسخة المبنية (aali-cli).",
                      "A source run cannot update itself — updates work in "
                      "the built aali-cli."),
    "disabled": ("التحديثات معطّلة (AALI_CLI_UPDATE_OFF=1).",
                 "Updates are disabled (AALI_CLI_UPDATE_OFF=1)."),
    "failed": ("فشل التحديث: {error}", "Update failed: {error}"),
    "rolled_back": ("استُعيد الإصدار السابق ({version}).",
                    "Restored the previous version ({version})."),
    "no_previous": ("لا يوجد إصدار سابق محفوظ.", "No previous version kept."),
    "auto_on": ("الفحص التلقائي عند التشغيل: مفعّل.",
                "Auto-check on launch: on."),
    "auto_off": ("الفحص التلقائي عند التشغيل: معطّل.",
                 "Auto-check on launch: off."),
    "channel_set": ("القناة الآن: {channel}", "Channel is now: {channel}"),
    "not_downloaded": ("لم يُنزَّل التحديث بعد — /update check أولاً.",
                       "Nothing downloaded yet — run /update check first."),
    "log_path": ("سجل التحديثات: {path}", "Update log: {path}"),
    "config_path": ("ملف الإعداد: {path}", "Config file: {path}"),
    "can_update": ("يمكنه التحديث الذاتي.", "Can self-update."),
    "cannot_update": ("لا يمكنه التحديث الذاتي: {reason}",
                      "Cannot self-update: {reason}"),
    "key_present": ("مفتاح التحقق: مثبّت", "Verification key: bundled"),
    "key_missing": ("مفتاح التحقق: غير مثبّت", "Verification key: missing"),
    "usage": ("/update check | apply | version | auto on|off | channel stable|beta",
              "/update check | apply | version | auto on|off | channel stable|beta"),
    "invalid": ("قيمة غير معروفة: {value}", "Unknown value: {value}"),
    "staged_path": ("الحزمة في: {path}", "Payload staged at: {path}"),
}


def t(key: str, lang: str = "ar", **fields: Any) -> str:
    """The message for *lang* — the ONLY place an update string is chosen."""
    pair = MSG.get(key)
    if pair is None:
        raise CliUpdateError(f"unknown message: {key}")
    index = 1 if lang == "en" else 0
    return pair[index].format(**fields)


def both(key: str, **fields: Any) -> tuple[str, str]:
    return MSG[key][0].format(**fields), MSG[key][1].format(**fields)


# ————— configuration —————
def config_file() -> Path:
    override = os.getenv("AALI_CLI_UPDATE_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".aali" / "cli_update.json"


def load_config() -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        loaded = json.loads(config_file().read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    config = {
        "auto_check": bool(data.get("auto_check", True)),
        "channel": str(data.get("channel") or "stable"),
        "hub_url": str(data.get("hub_url") or DEFAULT_HUB),
        "lang": str(data.get("lang") or "ar"),
    }
    if config["channel"] not in CHANNELS:
        config["channel"] = "stable"
    if config["lang"] not in LANGS:
        config["lang"] = "ar"
    return config


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    for key, value in (patch or {}).items():
        if key not in ("auto_check", "channel", "hub_url", "lang"):
            raise CliUpdateError(f"unknown update setting: {key}")
        if key == "auto_check":
            current["auto_check"] = bool(value)
        elif key == "lang":
            text = str(value or "").strip().lower()
            if text not in LANGS:
                raise CliUpdateError("lang must be ar | en | both")
            current["lang"] = text
        elif key == "channel":
            text = str(value or "").strip().lower()
            if text not in CHANNELS:
                raise CliUpdateError("channel must be stable | beta")
            current["channel"] = text
        else:
            text = str(value or "").strip()
            if len(text) > 300:
                raise CliUpdateError("hub url is too long")
            shared.check_secure_url(text or DEFAULT_HUB)
            current["hub_url"] = text or DEFAULT_HUB
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return current


# ————— the verification key —————
def public_key_hex() -> str:
    for name in ("AALI_CLI_UPDATE_PUBKEY", "AALI_UPDATE_PUBLIC_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    path = (os.getenv("AALI_CLI_UPDATE_PUBKEY_FILE") or "").strip()
    if path and Path(path).is_file():
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def public_key():
    hex_text = public_key_hex()
    return shared.pubkey_from_hex(hex_text) if hex_text else None


# ————— installation shape —————
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path | None:
    if is_frozen():
        return Path(sys.executable).resolve().parent / "updates"
    env = os.getenv("AALI_CLI_UPDATE_DIR")
    return Path(env) if env else None


def payload_name() -> str:
    return "aali-cli.exe" if shared.host_platform() == "win" else "aali-cli"


def can_update() -> tuple[bool, str]:
    """(ok, arabic reason). Checked before every check/download."""
    if os.getenv("AALI_CLI_UPDATE_OFF") == "1":
        return False, t("disabled")
    if install_dir() is None:
        return False, t("not_installed")
    if not public_key_hex():
        return False, t("no_key")
    return True, ""


# ————— job state —————
_LOCK = threading.Lock()
_JOB: dict[str, Any] = {"phase": "idle", "error": "", "version": "",
                        "started": 0.0, "staged": ""}
_LAST_CHECK: dict[str, float] = {"at": 0.0}
_UPDATE: dict[str, Any] = {}


def job_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_JOB)


def _set_job(**fields: Any) -> None:
    with _LOCK:
        _JOB.update(fields)


def reset_state() -> None:
    _UPDATE.clear()
    _LAST_CHECK["at"] = 0.0
    _JOB.update({"phase": "idle", "error": "", "version": "", "started": 0.0,
                 "staged": ""})


# ————— operations —————
def check(force: bool = False) -> dict[str, Any]:
    ok, reason = can_update()
    if not ok:
        return {"ok": False, "error": reason, "update_available": False,
                "checked": False}
    config = load_config()
    now = time.time()
    if not force and _UPDATE and \
            (now - _LAST_CHECK["at"]) < AUTO_CHECK_INTERVAL_S:
        return {**_UPDATE, "cached": True, "checked": False}
    _set_job(phase="checking", error="")
    try:
        result = shared.check_for_update(
            config["hub_url"], CLI_VERSION, app=APP_ID,
            platform=shared.host_platform(), channel=config["channel"],
            public_key=public_key(),
            token=os.getenv("AALI_UPDATE_TOKEN", "").strip())
    except shared.UpdateError as exc:
        _set_job(phase="idle", error=str(exc))
        return {"ok": False, "error": str(exc), "update_available": False,
                "checked": True}
    _LAST_CHECK["at"] = now
    target = install_dir()
    if target is not None:
        shared.configure(app=APP_ID, install_dir=target,
                         public_key=public_key(), hub_url=config["hub_url"])
    payload = {
        "ok": True,
        "checked": True,
        "update_available": bool(result.get("update_available")),
        "auto_apply_allowed": bool(result.get("auto_apply_allowed")),
        "latest_version": result.get("latest_version", ""),
        "min_version": result.get("min_version", ""),
        "release_notes_ar": result.get("release_notes_ar", ""),
        "release_notes_en": result.get("release_notes_en", ""),
        "url": result.get("absolute_url", ""),
        "sha256": result.get("sha256", ""),
        "artifact_signature": result.get("artifact_signature", ""),
    }
    if payload["update_available"] and not payload["auto_apply_allowed"]:
        payload["notice"] = "below-min-version"
    _UPDATE.clear()
    _UPDATE.update(payload)
    _set_job(phase="idle")
    return payload


def download_and_stage(force: bool = True) -> dict[str, Any]:
    """Download + verify + stage. The CLI never applies without asking."""
    ok, reason = can_update()
    if not ok:
        return {"ok": False, "error": reason}
    found = check(force=force)
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error", "")}
    if not found.get("update_available"):
        return {"ok": True, "phase": "idle", "message_key": "up_to_date"}
    target = install_dir()
    _set_job(phase="downloading", error="", started=time.time(),
             version=found.get("latest_version", ""))
    try:
        archive = shared.download_and_verify(
            found["url"], found["sha256"], found["artifact_signature"],
            public_key(), dest_dir=target / "downloads",
            token=os.getenv("AALI_UPDATE_TOKEN", "").strip(),
            max_bytes=MAX_ARCHIVE_BYTES)
        staged = shared.stage_update(archive, target,
                                     version=found.get("latest_version", ""))
    except shared.UpdateError as exc:
        _set_job(phase="error", error=str(exc))
        return {"ok": False, "error": str(exc)}
    _set_job(phase="staged", staged=str(staged), error="",
             version=found.get("latest_version", ""))
    return {"ok": True, "phase": "staged", "staged": str(staged),
            "message_key": "downloaded",
            "version": found.get("latest_version", "")}


def launch_payload(directory: Path) -> None:  # pragma: no cover - spawns a process
    """Start ``<directory>/aali-cli[.exe]`` with the same argv. Nothing else."""
    target = directory / payload_name()
    if not target.exists():
        candidates = sorted(directory.glob("aali-cli*"))
        if not candidates:
            raise CliUpdateError("payload executable missing after the update")
        target = candidates[0]
    argv = [str(target)] + list(sys.argv[1:])
    kwargs: dict[str, Any] = {"close_fds": True}
    if os.name == "nt":  # noqa: S603 - argv list, no shell, our own payload
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                   | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **kwargs)


def activate() -> dict[str, Any]:
    """Swap the staged payload in; the caller decides about the relaunch."""
    target = install_dir()
    staged = job_status().get("staged") or ""
    if not staged:
        return {"ok": False, "error_key": "not_downloaded"}
    current = target / shared.CURRENT_DIR
    try:
        shared.apply_and_restart(staged, target,
                                 spawn=lambda: launch_payload(current))
    except shared.UpdateError as exc:
        return {"ok": False, "error": str(exc)}
    _set_job(phase="activating")
    return {"ok": True, "phase": "activating"}


def schedule_exit(delay: float = 0.5) -> bool:
    """Leave this process so the relaunched one can take the terminal."""
    if os.getenv("AALI_CLI_UPDATE_NO_EXIT") == "1":
        return False
    timer = threading.Timer(delay, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    return True


def rollback() -> dict[str, Any]:
    target = install_dir()
    before = shared.read_state(target)
    try:
        shared.rollback_if_failed(target, force=True)
    except shared.UpdateError as exc:
        return {"ok": False, "error": str(exc)}
    after = shared.read_state(target)
    if after.get("version") == before.get("version") and \
            not after.get("rolled_back_from"):
        return {"ok": False, "error_key": "no_previous"}
    return {"ok": True, "version": after.get("version", "")}


def status() -> dict[str, Any]:
    config = load_config()
    ok, reason = can_update()
    target = install_dir()
    state = shared.read_state(target) if target else {}
    update = dict(_UPDATE)
    return {
        "ok": True,
        "app": APP_ID,
        "version": CLI_VERSION,
        "version_string": f"v{CLI_VERSION}",
        "platform": shared.host_platform(),
        "config": config,
        "config_file": str(config_file()),
        "can_update": ok,
        "reason": reason,
        "has_public_key": bool(public_key_hex()),
        "update_available": bool(update.get("update_available")),
        "auto_apply_allowed": bool(update.get("auto_apply_allowed")),
        "latest_version": update.get("latest_version", ""),
        "release_notes_ar": update.get("release_notes_ar", ""),
        "release_notes_en": update.get("release_notes_en", ""),
        "notice": update.get("notice", ""),
        "checked": bool(update.get("checked")),
        "last_check": _LAST_CHECK["at"],
        "job": job_status(),
        "current_version_on_disk": state.get("version", ""),
        "previous_version": state.get("previous", ""),
        "log_path": str(shared.update_log_path()),
    }


def startup() -> dict[str, Any]:
    """First call of every launch: count an un-acked launch, roll back at two."""
    target = install_dir()
    if target is None:
        return {"status": "clean", "version": ""}
    try:
        return shared.register_startup(target)
    except shared.UpdateError:
        return {"status": "clean", "version": ""}


def launch_ok() -> None:
    target = install_dir()
    if target is not None:
        try:
            shared.record_launch_ok(target)
        except shared.UpdateError:
            pass


def auto_check(announce: Any = None) -> dict[str, Any] | None:
    """The launch-time check. Background-safe; honours the owner's toggle.

    ``announce`` is called with the AR/EN pair when a release is waiting, so
    the CLI can print it in the right language without this module knowing
    anything about `paint`.
    """
    if not load_config().get("auto_check"):
        return None
    result = check(force=False)
    if announce and result.get("ok") and result.get("update_available"):
        announce(both("available", version=result.get("latest_version", "")))
    return result


def start_background_check(announce: Any = None) -> threading.Thread:
    """Fire-and-forget launch check so the banner is never delayed by HTTP."""
    thread = threading.Thread(target=auto_check, args=(announce,),
                              name="aali-cli-update", daemon=True)
    thread.start()
    return thread


# ————— the /update command —————
def run_command(arg: str, *, say: Any, color: bool = True,
                confirm: Any = None) -> list[str]:
    """Handle ``/update …``.

    ``say(tag, text, style)`` is the CLI's own printer; ``confirm(prompt)`` is
    an injected y/N ask (input() in the REPL) so this stays testable. Returns
    the lines it printed, which is what the tests assert on.
    """
    config = load_config()
    lang = config["lang"]
    parts = str(arg or "").strip().split()
    verb = parts[0].lower() if parts else "status"
    rest = parts[1:]

    def emit(key: str, style: str = "info", **fields: Any) -> None:
        if lang == "both":
            arabic, english = both(key, **fields)
            say("✻", arabic, style)
            say(" ", english, "dim")
        else:
            say("✻", t(key, lang, **fields), style)

    if verb in ("", "status"):
        lines = _status_lines(lang)
        for line in lines:
            say(" ", line, "dim")
        return lines

    if verb == "version":
        info = status()
        latest = info.get("latest_version") or ""
        pairs = [("current", {"version": info["version"]})]
        pairs.append(("latest", {"version": latest}) if latest
                     else ("latest_none", {}))
        lines: list[str] = []
        for key, fields in pairs:
            arabic, english = both(key, **fields)
            if lang == "both":
                say("✻" if not lines else " ", arabic,
                    "info" if not lines else "dim")
                say(" ", english, "dim")
                lines.extend([arabic, english])
            else:
                text = arabic if lang == "ar" else english
                say("✻" if not lines else " ", text,
                    "info" if not lines else "dim")
                lines.append(text)
        return lines

    if verb == "check":
        emit("checking", "cyan")
        result = check(force=True)
        if not result.get("ok"):
            emit("failed", "warn", error=result.get("error", ""))
            return [result.get("error", "")]
        if not result.get("update_available"):
            emit("up_to_date", "green")
            return ["up-to-date"]
        lines = [t("available", lang,
                   version=result.get("latest_version", ""))]
        say("✻", lines[0], "gold")
        notes = str(result.get("release_notes_ar") or
                    result.get("release_notes_en") or "").strip()
        if notes:
            say(" ", notes[:600], "dim")
            lines.append(notes[:600])
        if result.get("notice"):
            emit("manual_only", "warn")
            lines.append("manual-only")
        return lines

    if verb == "apply":
        result = download_and_stage()
        if not result.get("ok"):
            key = result.get("error_key")
            emit(key, "warn") if key else emit("failed", "warn",
                                               error=result.get("error", ""))
            return [result.get("error", key or "")]
        if result.get("phase") != "staged":
            emit("up_to_date", "green")
            return ["up-to-date"]
        emit("downloaded", "green")
        apply_result = activate()
        if not apply_result.get("ok"):
            key = apply_result.get("error_key")
            emit(key, "warn") if key else emit("failed", "warn",
                                               error=apply_result.get("error", ""))
            return [apply_result.get("error", key or "")]
        say(" ", t("staged_path", lang, path=result["staged"]), "dim")
        if confirm is None:
            return ["staged", result["staged"]]
        answer = confirm(t("restart_prompt", lang))
        if str(answer).strip().lower() in ("y", "yes", "ن", "نعم"):
            emit("restarted", "gold")
            schedule_exit()
            return ["restarting"]
        return ["staged"]

    if verb == "auto":
        value = (rest[0].lower() if rest else "")
        if value not in ("on", "off"):
            emit("invalid", "warn", value=value or "?")
            return []
        save_config({"auto_check": value == "on"})
        emit("auto_on" if value == "on" else "auto_off",
             "green" if value == "on" else "dim")
        return [value]

    if verb == "channel":
        value = (rest[0].lower() if rest else "")
        if value not in CHANNELS:
            emit("invalid", "warn", value=value or "?")
            return []
        save_config({"channel": value})
        emit("channel_set", "info", channel=CHANNEL_LABELS[value])
        return [value]

    if verb == "rollback":
        result = rollback()
        if not result.get("ok"):
            key = result.get("error_key")
            emit(key, "warn") if key else emit("failed", "warn",
                                               error=result.get("error", ""))
            return [key or ""]
        emit("rolled_back", "green", version=result.get("version", ""))
        return [result.get("version", "")]

    emit("usage", "warn")
    return ["usage"]


def _status_lines(lang: str) -> list[str]:
    info = status()
    lines = [
        t("current", lang, version=info["version"]),
        (t("can_update", lang) if info["can_update"]
         else t("cannot_update", lang, reason=info["reason"])),
        (t("key_present", lang) if info["has_public_key"]
         else t("key_missing", lang)),
        t("log_path", lang, path=info["log_path"]),
        t("config_path", lang, path=info["config_file"]),
    ]
    if info["update_available"]:
        lines.insert(1, t("available", lang,
                          version=info["latest_version"]))
    return lines