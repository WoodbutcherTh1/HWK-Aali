"""آلي ستوديو — the Studio side of the universal updater.

Thin on purpose. Every security decision (verify, refuse, zip-slip, retain the
previous version) lives in :mod:`shared.updater`; this module only knows:

* WHERE this client is installed and what its payload is called,
* what the owner configured (``%APPDATA%/AaliStudio/config.json`` — auto-check
  on launch, channel, hub URL),
* how to say it in Arabic,
* how to relaunch **this** app after a swap.

Two honest limits, stated in the UI rather than hidden:

* A **source run** (``python studio_app.py``) cannot update itself — there is
  no installed artifact to replace. ``can_update()`` says so in Arabic and the
  check button reports it instead of pretending.
* No public key provisioned means **no updates at all**. A client that fetched
  its verification key from the hub would trust whatever the hub served, so an
  unkeyed build refuses every manifest rather than showing a cheerful,
  unverified "update available".

Activation model (works for a onefile .exe AND a macOS .app bundle): the
verified payload is staged, then swapped into ``<install>/updates/current/``
and **that** copy is launched. The running binary is never overwritten while
it is running, and the previous payload is retained for rollback.
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

HERE = Path(__file__).resolve().parent
_REPO = HERE.parents[1]
for _extra in (_REPO / "file-agent", _REPO / "scripts", HERE):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import version as studio_version  # noqa: E402
from shared import updater as shared  # noqa: E402

try:
    import models_proxy as mp  # noqa: E402
except ImportError:  # pragma: no cover - packaging guard
    mp = None  # type: ignore[assignment]

APP_ID = studio_version.APP_ID
DEFAULT_HUB = "aali.dpdns.org"
CHANNELS = ("stable", "beta")
#: An auto-check that fires on every launch would hammer a hub that is
#: probably asleep; six hours is the same shape as pip's cache window.
AUTO_CHECK_INTERVAL_S = 6 * 3600
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


# ————— messages (Arabic-first, one place) —————
MSG = {
    "up_to_date": "أنت على أحدث إصدار.",
    "available": "إصدار جديد متوفر",
    "checking": "جارٍ البحث عن تحديثات…",
    "downloading": "جارٍ التنزيل والتحقق…",
    "downloaded": "تم تنزيل التحديث.",
    "no_key": "لا يوجد مفتاح تحقق مثبّت في هذا البناء — التحديثات معطّلة "
              "حتى يبنيه المالك بمفتاح Ed25519 العام.",
    "not_installed": "هذه نسخة تُشغَّل من المصدر — لا يمكنها تحديث نفسها. "
                     "التحديث يعمل على النسخة المبنية (Aali-Studio).",
    "disabled": "التحديثات معطّلة (AALI_STUDIO_UPDATE_OFF=1).",
    "manual_only": "هذا الإصدار يحتاج ترقية يدوية (تجاوز الحد الأدنى).",
    "mismatch": "الإصدار المنشور يخصّ نظاماً آخر — تم تجاهله.",
    "failed": "فشل التحديث: ",
    "restarting": "سيُعاد تشغيل آلي ستوديو الآن…",
    "rolled_back": "استُعيد الإصدار السابق.",
    "no_previous": "لا يوجد إصدار سابق محفوظ.",
}


class StudioUpdateError(Exception):
    """Expected, user-facing (rendered in Arabic by the client)."""


# ————— configuration —————
def config_file() -> Path:
    """``%APPDATA%/AaliStudio/config.json`` (macOS: Application Support)."""
    base = mp.cfg_dir() if mp is not None else str(
        Path.home() / ".AaliStudio")
    return Path(base) / "config.json"


def load_config() -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        with config_file().open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    config = {
        "auto_check": bool(data.get("auto_check", True)),   # default ON
        "channel": str(data.get("channel") or "stable"),
        "hub_url": str(data.get("hub_url") or DEFAULT_HUB),
    }
    if config["channel"] not in CHANNELS:
        config["channel"] = "stable"
    return config


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    for key, value in (patch or {}).items():
        if key not in ("auto_check", "channel", "hub_url"):
            raise StudioUpdateError(f"إعداد تحديث غير معروف: {key}")
        if key == "auto_check":
            current["auto_check"] = bool(value)
        elif key == "channel":
            text = str(value or "").strip().lower()
            if text not in CHANNELS:
                raise StudioUpdateError(
                    "القناة يجب أن تكون stable أو beta")
            current["channel"] = text
        else:
            text = str(value or "").strip()
            if len(text) > 300:
                raise StudioUpdateError("عنوان المركز طويل جداً")
            # Validate through the shared policy so the stored value is
            # already the exact string the client will use, and so an
            # http://hub.example entry can never be persisted by accident —
            # the owner should learn that HERE, not three clicks later.
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
        pass  # Windows keeps the AppData ACL
    return current


# ————— installation shape —————
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path | None:
    """Where the update machinery lives, or None for a source run."""
    if is_frozen():
        return Path(sys.executable).resolve().parent / "updates"
    env = os.getenv("AALI_STUDIO_UPDATE_DIR")
    if env:
        return Path(env)  # tests + a developer staging area
    return None


def payload_name() -> str:
    """The artifact name inside the release zip for THIS platform."""
    if shared.host_platform() == "macos":
        return "Aali-Studio.app"
    if shared.host_platform() == "win":
        return "Aali-Studio.exe"
    return "Aali-Studio"


def can_update() -> tuple[bool, str]:
    """(ok, Arabic reason). Checked before every check/download."""
    if os.getenv("AALI_STUDIO_UPDATE_OFF") == "1":
        return False, MSG["disabled"]
    if install_dir() is None:
        return False, MSG["not_installed"]
    if studio_version.public_key_hex() == "":
        return False, MSG["no_key"]
    return True, ""


# ————— job state (the download is a background thread) —————
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


# ————— the shared-module bridge —————
def _public_key() -> Any:
    return studio_version.public_key()


def check(force: bool = False) -> dict[str, Any]:
    """Ask the hub whether a newer signed release exists.

    Throttled unless *force* (the status-bar click and the settings button
    both pass force=True).
    """
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
            config["hub_url"], studio_version.__version__,
            app=APP_ID, platform=shared.host_platform(),
            channel=config["channel"], public_key=_public_key(),
            token=os.getenv("AALI_UPDATE_TOKEN", "").strip(),
            timeout=20.0)
    except shared.UpdateError as exc:
        _set_job(phase="idle", error=str(exc))
        return {"ok": False, "error": MSG["failed"] + str(exc),
                "update_available": False, "checked": True}
    _LAST_CHECK["at"] = now
    shared.configure(app=APP_ID, install_dir=install_dir(),
                     public_key=_public_key(), hub_url=config["hub_url"])
    payload = {
        "ok": True,
        "checked": True,
        "update_available": bool(result.get("update_available")),
        "auto_apply_allowed": bool(result.get("auto_apply_allowed")),
        "latest_version": result.get("latest_version", ""),
        "min_version": result.get("min_version", ""),
        "release_notes_ar": result.get("release_notes_ar", ""),
        "release_notes_en": result.get("release_notes_en", ""),
        "reason": result.get("reason", ""),
        "url": result.get("absolute_url", ""),
        "sha256": result.get("sha256", ""),
        "artifact_signature": result.get("artifact_signature", ""),
    }
    if payload["update_available"] and not payload["auto_apply_allowed"]:
        payload["notice"] = MSG["manual_only"]
    _UPDATE.clear()
    _UPDATE.update(payload)
    _set_job(phase="idle")
    return payload


def start_download() -> dict[str, Any]:
    """Download + verify + stage, in a background thread.

    Returns immediately with the job phase so the UI can show progress; the
    result lands in :func:`job_status` and ``POST /api/update/check`` is
    re-issued by the client when the phase flips to ``staged``.
    """
    ok, reason = can_update()
    if not ok:
        return {"ok": False, "error": reason}
    if job_status()["phase"] == "downloading":
        return {"ok": True, "phase": "downloading"}
    found = check(force=True)
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error", "")}
    if not found.get("update_available"):
        return {"ok": True, "phase": "idle",
                "message": MSG["up_to_date"]}
    _set_job(phase="downloading", error="", started=time.time(),
             version=found.get("latest_version", ""))
    thread = threading.Thread(target=_download_worker, args=(dict(found),),
                              name="aali-studio-update", daemon=True)
    thread.start()
    return {"ok": True, "phase": "downloading",
            "version": found.get("latest_version", "")}


def _download_worker(found: dict[str, Any]) -> None:
    config = load_config()
    target = install_dir()
    try:
        archive = shared.download_and_verify(
            found["url"], found["sha256"], found["artifact_signature"],
            _public_key(), dest_dir=target / "downloads",
            token=os.getenv("AALI_UPDATE_TOKEN", "").strip(),
            max_bytes=MAX_ARCHIVE_BYTES)
        staged = shared.stage_update(archive, target,
                                     version=found.get("latest_version", ""))
        _set_job(phase="staged", staged=str(staged),
                 version=found.get("latest_version", ""),
                 error="")
    except shared.UpdateError as exc:
        _set_job(phase="error", error=MSG["failed"] + str(exc))
    except Exception as exc:  # noqa: BLE001 - a crash must still be reportable
        _set_job(phase="error", error=MSG["failed"] + f"{type(exc).__name__}")


def schedule_exit(delay: float = 0.5) -> bool:
    """End this process once the activation response has flushed.

    The timer (not an inline exit) is deliberate: the browser must receive the
    JSON answer before the process goes away, or the client shows a network
    error instead of "restarting now".

    Returns whether the exit was actually scheduled. ``AALI_STUDIO_UPDATE_NO_EXIT=1``
    suppresses it — the guard the test suite and any automated run needs, the
    same shape as the shared module's ``AALI_UPDATE_NO_RESTART``. Without it an
    activate test would take the WHOLE test runner down with ``os._exit``.
    """
    if os.getenv("AALI_STUDIO_UPDATE_NO_EXIT") == "1":
        return False
    timer = threading.Timer(delay, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    return True


def launch_payload(directory: Path) -> None:  # pragma: no cover - spawns a GUI
    """Start the payload in *directory* — never anything else.

    The only executable this ever starts is ``<directory>/Aali-Studio[.exe|.app]``,
    a path this module BUILDS from its own constants. There is no path where a
    downloaded filename reaches a process launch.
    """
    name = payload_name()
    if shared.host_platform() == "macos":
        target = directory / name
        argv = ["open", str(target)] if target.exists() else [
            "open", str(directory)]
    else:
        target = directory / name
        if not target.exists():
            candidates = sorted(directory.glob("Aali-Studio*"))
            if not candidates:
                raise StudioUpdateError("الملف التنفيذي غير موجود بعد التحديث")
            target = candidates[0]
        argv = [str(target)]
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
    """Swap the staged payload in and relaunch Studio from ``current/``."""
    target = install_dir()
    staged = job_status().get("staged") or ""
    if not staged:
        found = check(force=True)
        if not found.get("update_available"):
            return {"ok": False, "error": MSG["up_to_date"]}
        return {"ok": False, "error": "لم يُنزَّل التحديث بعد."}
    current = target / shared.CURRENT_DIR

    def spawn() -> None:
        launch_payload(current)

    try:
        shared.apply_and_restart(staged, target, spawn=spawn)
    except shared.UpdateError as exc:
        return {"ok": False, "error": MSG["failed"] + str(exc)}
    _set_job(phase="activating")
    return {"ok": True, "phase": "activating", "message": MSG["restarting"]}


def rollback() -> dict[str, Any]:
    target = install_dir()
    try:
        shared.rollback_if_failed(target, force=True)
    except shared.UpdateError as exc:
        return {"ok": False, "error": str(exc)}
    state = shared.read_state(target)
    if state.get("version") == "" and not state:
        return {"ok": False, "error": MSG["no_previous"]}
    return {"ok": True, "message": MSG["rolled_back"],
            "version": state.get("version", "")}


# ————— status for the UI + startup hooks —————
def status() -> dict[str, Any]:
    config = load_config()
    ok, reason = can_update()
    target = install_dir()
    state = shared.read_state(target) if target else {}
    update = dict(_UPDATE)
    return {
        "ok": True,
        "app": APP_ID,
        "version": studio_version.__version__,
        "version_string": studio_version.version_string(),
        "build_date": studio_version.BUILD_DATE,
        "commit": studio_version.COMMIT,
        "platform": shared.host_platform(),
        "config": config,
        "config_file": str(config_file()),
        "can_update": ok,
        "reason": reason,
        "has_public_key": bool(studio_version.public_key_hex()),
        "update_available": bool(update.get("update_available")),
        "auto_apply_allowed": bool(update.get("auto_apply_allowed")),
        "latest_version": update.get("latest_version", ""),
        "release_notes_ar": update.get("release_notes_ar", ""),
        "release_notes_en": update.get("release_notes_en", ""),
        "notice": update.get("notice", ""),
        "reason_code": update.get("reason", ""),
        "checked": bool(update.get("checked")),
        "last_check": _LAST_CHECK["at"],
        "job": job_status(),
        "current_version_on_disk": state.get("version", ""),
        "previous_version": state.get("previous", ""),
        "pending_ack": bool(state.get("pending_ack")),
        "failures": int(state.get("failures", 0) or 0),
        "log_path": str(shared.update_log_path()),
        "messages": MSG,
    }


def startup() -> dict[str, Any]:
    """First thing the app calls: count an un-acked launch, roll back at two."""
    target = install_dir()
    if target is None:
        return {"status": "clean", "version": ""}
    try:
        result = shared.register_startup(target)
    except shared.UpdateError:
        return {"status": "clean", "version": ""}
    shared.configure(app=APP_ID, install_dir=target)
    return result


def launch_ok() -> None:
    """Call once the UI is really up — the new payload is accepted."""
    target = install_dir()
    if target is not None:
        try:
            shared.record_launch_ok(target)
        except shared.UpdateError:
            pass


def auto_check() -> dict[str, Any] | None:
    """The launch-time check, honouring the owner's toggle."""
    config = load_config()
    if not config.get("auto_check"):
        return None
    return check(force=False)