"""آلي Desktop — the auto-update bridge (shared updater + Desktop specifics).

Same contract as Studio (``build-desktop/aali-studio/updater.py``), because
both sit on the same :mod:`shared.updater`: never trust the hub, verify
SHA256 + Ed25519 client-side, refuse zip-slip, keep the previous version for
rollback, never execute a download.

What is Desktop-specific:

* its own config file — ``%APPDATA%/AaliDesktop/config.json`` (macOS:
  ``~/Library/Application Support/AaliDesktop/config.json``),
* its payload name (``Aali-Desktop.exe`` / ``Aali-Desktop.app``),
* its UI is JavaScript INJECTED into Aali's own web UI by
  ``aali_desktop_app.EXTRAS_JS`` — there is no HTML template here, so every
  string handed to that JS is built and escaped on THIS side.

The pre-existing "the server runs a newer build" pill
(``/api/desktop-version``) is a DIFFERENT thing — it notices a newer *served*
build and offers a download link. It is left alone: it answers "is the brain
behind this window newer?", while this module answers "is this window itself
outdated?".
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _extra in (_HERE / "file-agent", _HERE / "scripts", _HERE):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from file_agent import hwk_paths  # noqa: E402
from shared import updater as shared  # noqa: E402

APP_ID = "desktop"
DEFAULT_HUB = "aali.dpdns.org"
CHANNELS = ("stable", "beta")
CHANNEL_LABELS = {"stable": "مستقر", "beta": "تجريبي"}
AUTO_CHECK_INTERVAL_S = 6 * 3600
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

MSG = {
    "up_to_date": "أنت على أحدث إصدار.",
    "available": "تحديث جديد متوفر",
    "checking": "جارٍ البحث عن تحديثات…",
    "downloading": "جارٍ التنزيل والتحقق…",
    "downloaded": "تم تنزيل التحديث.",
    "no_key": "لا يوجد مفتاح تحقق مثبّت في هذا البناء — التحديثات معطّلة.",
    "not_installed": "هذه نسخة تُشغَّل من المصدر — لا يمكنها تحديث نفسها.",
    "disabled": "التحديثات معطّلة (AALI_DESKTOP_UPDATE_OFF=1).",
    "manual_only": "هذا الإصدار يحتاج ترقية يدوية.",
    "failed": "فشل التحديث: ",
    "restarting": "سيُعاد تشغيل آلي Desktop الآن…",
    "rolled_back": "استُعيد الإصدار السابق.",
    "no_previous": "لا يوجد إصدار سابق محفوظ.",
}


class DesktopUpdateError(Exception):
    """Expected, user-facing (rendered in Arabic by the injected JS)."""


# ————— the version —————
def app_version() -> str:
    """Read APP_VERSION from the app module without importing its window code."""
    try:
        import aali_desktop_app
        return str(getattr(aali_desktop_app, "APP_VERSION", "0.0.0"))
    except Exception:  # noqa: BLE001 - frozen builds / partial checkouts
        return os.getenv("AALI_DESKTOP_VERSION", "0.0.0")


def public_key_hex() -> str:
    """The bundled Ed25519 PUBLIC key, or '' (which disables updates).

    Order: ``AALI_DESKTOP_UPDATE_PUBKEY`` / ``AALI_UPDATE_PUBLIC_KEY`` (what the
    build bakes in) -> ``AALI_DESKTOP_UPDATE_PUBKEY_FILE`` (the owner's PC) ->
    ``<data root>/aali-hub-secrets/update_pub_hex.txt``.
    """
    for name in ("AALI_DESKTOP_UPDATE_PUBKEY", "AALI_UPDATE_PUBLIC_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    path = (os.getenv("AALI_DESKTOP_UPDATE_PUBKEY_FILE") or "").strip()
    candidates = [Path(path)] if path else []
    try:
        candidates.append(hwk_paths.data_root() / "aali-hub-secrets" /
                          "update_pub_hex.txt")
    except Exception:  # noqa: BLE001
        pass
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def public_key():
    hex_text = public_key_hex()
    return shared.pubkey_from_hex(hex_text) if hex_text else None


# ————— configuration —————
def config_file() -> Path:
    return Path(hwk_paths.config_dir("AaliDesktop")) / "config.json"


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
    }
    if config["channel"] not in CHANNELS:
        config["channel"] = "stable"
    return config


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    for key, value in (patch or {}).items():
        if key not in ("auto_check", "channel", "hub_url"):
            raise DesktopUpdateError(f"إعداد تحديث غير معروف: {key}")
        if key == "auto_check":
            current["auto_check"] = bool(value)
        elif key == "channel":
            text = str(value or "").strip().lower()
            if text not in CHANNELS:
                raise DesktopUpdateError("القناة يجب أن تكون stable أو beta")
            current["channel"] = text
        else:
            text = str(value or "").strip()
            if len(text) > 300:
                raise DesktopUpdateError("عنوان المركز طويل جداً")
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


# ————— installation shape —————
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path | None:
    if is_frozen():
        return Path(sys.executable).resolve().parent / "updates"
    env = os.getenv("AALI_DESKTOP_UPDATE_DIR")
    return Path(env) if env else None


def payload_name() -> str:
    return {"win": "Aali-Desktop.exe",
            "macos": "Aali-Desktop.app"}.get(shared.host_platform(),
                                             "Aali-Desktop")


def can_update() -> tuple[bool, str]:
    if os.getenv("AALI_DESKTOP_UPDATE_OFF") == "1":
        return False, MSG["disabled"]
    if install_dir() is None:
        return False, MSG["not_installed"]
    if not public_key_hex():
        return False, MSG["no_key"]
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
    """Test seam: forget the cached check between cases."""
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
            config["hub_url"], app_version(), app=APP_ID,
            platform=shared.host_platform(), channel=config["channel"],
            public_key=public_key(),
            token=os.getenv("AALI_UPDATE_TOKEN", "").strip())
    except shared.UpdateError as exc:
        _set_job(phase="idle", error=str(exc))
        return {"ok": False, "error": MSG["failed"] + str(exc),
                "update_available": False, "checked": True}
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
        payload["notice"] = MSG["manual_only"]
    _UPDATE.clear()
    _UPDATE.update(payload)
    _set_job(phase="idle")
    return payload


def start_download() -> dict[str, Any]:
    ok, reason = can_update()
    if not ok:
        return {"ok": False, "error": reason}
    if job_status()["phase"] == "downloading":
        return {"ok": True, "phase": "downloading"}
    found = check(force=True)
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error", "")}
    if not found.get("update_available"):
        return {"ok": True, "phase": "idle", "message": MSG["up_to_date"]}
    _set_job(phase="downloading", error="", started=time.time(),
             version=found.get("latest_version", ""))
    threading.Thread(target=_download_worker, args=(dict(found),),
                     name="aali-desktop-update", daemon=True).start()
    return {"ok": True, "phase": "downloading",
            "version": found.get("latest_version", "")}


def _download_worker(found: dict[str, Any]) -> None:
    target = install_dir()
    try:
        archive = shared.download_and_verify(
            found["url"], found["sha256"], found["artifact_signature"],
            public_key(), dest_dir=target / "downloads",
            token=os.getenv("AALI_UPDATE_TOKEN", "").strip(),
            max_bytes=MAX_ARCHIVE_BYTES)
        staged = shared.stage_update(archive, target,
                                     version=found.get("latest_version", ""))
        _set_job(phase="staged", staged=str(staged), error="",
                 version=found.get("latest_version", ""))
    except shared.UpdateError as exc:
        _set_job(phase="error", error=MSG["failed"] + str(exc))
    except Exception as exc:  # noqa: BLE001 - a crash must still be reportable
        _set_job(phase="error", error=MSG["failed"] + type(exc).__name__)


def schedule_exit(delay: float = 0.6) -> bool:
    """End this process once the activation response has flushed.

    On a timer, never inline: the page must receive the JSON answer first or
    it shows a network error instead of "restarting". ``
    AALI_DESKTOP_UPDATE_NO_EXIT=1`` suppresses it — the guard the test suite
    and any automated run needs (an unguarded os._exit takes the whole runner
    down, silently, with exit code 0).
    """
    if os.getenv("AALI_DESKTOP_UPDATE_NO_EXIT") == "1":
        return False
    timer = threading.Timer(delay, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    return True


def launch_payload(directory: Path) -> None:  # pragma: no cover - spawns a GUI
    """Start ``<directory>/Aali-Desktop[.exe|.app]`` — never anything else."""
    target = directory / payload_name()
    if shared.host_platform() == "macos":
        argv = ["open", str(target if target.exists() else directory)]
    else:
        if not target.exists():
            candidates = sorted(directory.glob("Aali-Desktop*"))
            if not candidates:
                raise DesktopUpdateError("الملف التنفيذي غير موجود بعد التحديث")
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
    """Swap the staged payload in and relaunch from ``current/``."""
    target = install_dir()
    staged = job_status().get("staged") or ""
    if not staged:
        return {"ok": False, "error": "لم يُنزَّل التحديث بعد."}
    current = target / shared.CURRENT_DIR
    try:
        shared.apply_and_restart(staged, target,
                                 spawn=lambda: launch_payload(current))
    except shared.UpdateError as exc:
        return {"ok": False, "error": MSG["failed"] + str(exc)}
    _set_job(phase="activating")
    return {"ok": True, "phase": "activating", "message": MSG["restarting"]}


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
        return {"ok": False, "error": MSG["no_previous"]}
    return {"ok": True, "message": MSG["rolled_back"],
            "version": after.get("version", "")}


def status() -> dict[str, Any]:
    config = load_config()
    ok, reason = can_update()
    target = install_dir()
    state = shared.read_state(target) if target else {}
    update = dict(_UPDATE)
    return {
        "ok": True,
        "app": APP_ID,
        "version": app_version(),
        "version_string": f"v{app_version()}",
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
        "messages": MSG,
    }


def startup() -> dict[str, Any]:
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


def auto_check() -> dict[str, Any] | None:
    if not load_config().get("auto_check"):
        return None
    return check(force=False)


# ————— the UI strings handed to the injected JS —————
def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def status_chip_html(info: dict[str, Any]) -> str:
    """[1] ``v1.0.3 ✓`` — click to check."""
    if not info.get("can_update"):
        mark, cls, title = "—", "off", str(info.get("reason") or "")
    elif info.get("update_available"):
        mark, cls, title = "●", "new", MSG["available"]
    elif info.get("checked"):
        mark, cls, title = "✓", "ok", MSG["up_to_date"]
    else:
        mark, cls, title = "○", "idle", "اضغط للتحقق من التحديثات"
    return (f'<button id="aaliv" class="aali-pill aali-v {cls}" '
            f'title="{esc(title)}">{esc(info["version_string"])} {mark}</button>')


def banner_html(info: dict[str, Any]) -> str:
    """[2] «🎉 إصدار جديد v1.1.0 متوفر» + [تحديث الآن] [لاحقاً]."""
    if not info.get("update_available"):
        return ""
    notes = str(info.get("release_notes_ar") or
                info.get("release_notes_en") or "").strip()
    disabled = "" if info.get("auto_apply_allowed") else " disabled"
    return (
        '<div id="aalibanner" class="aali-banner">'
        f'<b>🎉 إصدار جديد v{esc(info.get("latest_version"))} متوفر</b>'
        + (f'<div class="aali-notes">{esc(notes[:600])}</div>' if notes else "")
        + f'<div class="aali-btns">'
          f'<button id="aalibuy" class="aali-copy"{disabled}>تحديث الآن</button>'
          f'<button id="aalilater" class="aali-hide">لاحقاً</button>'
          f'</div></div>')


def settings_html(info: dict[str, Any]) -> str:
    """[3] the settings box: version, check, auto toggle, channel, hub URL."""
    config = dict(info.get("config") or {})
    checked = " checked" if config.get("auto_check") else ""
    options = "".join(
        f'<option value="{esc(key)}"'
        f'{" selected" if config.get("channel") == key else ""}>'
        f'{esc(label)}</option>' for key, label in CHANNEL_LABELS.items())
    disabled = "" if info.get("can_update") else " disabled"
    warn = f'<div class="aali-warn">{esc(info.get("reason"))}</div>' \
        if info.get("reason") else ""
    return (
        '<div id="aalivbox" class="aali-box">'
        f'<b>التحديثات — آلي Desktop</b>'
        f'{warn}'
        f'<div>الإصدار الحالي: <b>{esc(info["version_string"])}</b></div>'
        f'<label class="aali-row"><span>تحقّق تلقائي عند التشغيل</span>'
        f'<input id="aalivauto" type="checkbox"{checked}></label>'
        f'<label class="aali-row"><span>القناة</span>'
        f'<select id="aalivchan">{options}</select></label>'
        f'<label class="aali-row"><span>مركز التحديثات</span>'
        f'<input id="aalivhub" dir="ltr" value="{esc(config.get("hub_url"))}">'
        '</label>'
        f'<div class="aali-dim">السجل: {esc(info.get("log_path"))}</div>'
        f'<div class="aali-btns">'
        f'<button id="aalivcheck" class="aali-hide"{disabled}>تحقّق</button>'
        f'<button id="aalivsave" class="aali-copy"{disabled}>حفظ</button>'
        f'<button id="aalivclose" class="aali-hide">إغلاق</button>'
        '</div></div>')


def done_dialog_html(info: dict[str, Any]) -> str:
    """[4] «تم تنزيل التحديث.» + [إعادة التشغيل الآن] [لاحقاً]"""
    version = esc(info.get("latest_version") or
                  (info.get("job") or {}).get("version") or "")
    return (
        '<div id="aalidone" class="aali-modal">'
        '<div class="aali-box">'
        f'<b>تم تنزيل التحديث</b><div>الإصدار v{version} تم التحقق منه.</div>'
        '<div class="aali-dim">لو فشل الإقلاع مرتين سيعود التطبيق تلقائياً '
        'إلى الإصدار السابق.</div>'
        '<div class="aali-btns">'
        '<button id="aalidonelater" class="aali-hide">لاحقاً</button>'
        '<button id="aalidonerestart" class="aali-copy">إعادة التشغيل الآن</button>'
        '</div></div></div>')


def ui_payload() -> dict[str, str]:
    """Everything the injected JS needs, in one call."""
    info = status()
    return {
        "chip": status_chip_html(info),
        "banner": banner_html(info),
        "settings": settings_html(info),
        "dialog": done_dialog_html(info),
    }


#: Injected into Aali's web UI by aali_desktop_app, next to its own EXTRAS_JS.
#: It never builds a string of its own — every piece of Arabic copy and every
#: id comes from the Python above, so there is ONE copy to keep correct.
UPDATE_JS = r"""
(function(){
  if (window.__aaliUpdate) return; window.__aaliUpdate = true;
  var api = window.pywebview && window.pywebview.api;
  if (!api || !api.update_ui) return;

  var st = document.createElement('style');
  st.textContent = [
    '.aali-v{margin-bottom:6px;font-variant-numeric:tabular-nums}',
    '.aali-v.ok{color:#8fd08f}.aali-v.new{background:#e8b34b;color:#241a05;border-color:#e8b34b}',
    '.aali-v.off,.aali-v.idle{opacity:.65}',
    '.aali-banner{position:fixed;top:0;inset-inline:0;z-index:99999;',
    'display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 16px;',
    'background:rgba(232,179,75,.16);border-bottom:1px solid rgba(232,179,75,.4);',
    'backdrop-filter:blur(6px);font-family:Tajawal,system-ui,sans-serif;font-size:13px}',
    '.aali-banner b{color:#f5cf8a}.aali-notes{opacity:.85;font-size:12px}',
    '.aali-row{display:flex;justify-content:space-between;gap:8px;margin:6px 0}',
    '.aali-row input,.aali-row select{background:#23242b;color:#f2f2f3;border:1px solid #2a2c35;border-radius:6px;padding:3px 6px;font:inherit}',
    '.aali-dim{opacity:.6;font-size:11.5px;direction:ltr;text-align:left}',
    '.aali-warn{color:#f5cf8a;font-size:12px;margin:4px 0}',
    '.aali-modal{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;',
    'justify-content:center;background:rgba(0,0,0,.55)}',
    '.aali-modal .aali-box{max-width:340px}'
  ].join('');
  document.head.appendChild(st);

  var box = null, modal = null;
  function closeBox(){ if(box){ box.remove(); box = null; } }
  function closeModal(){ if(modal){ modal.remove(); modal = null; } }

  function wire(sel, fn){ var n = document.querySelector(sel); if(n) n.onclick = fn; }

  function mount(){
    api.update_ui().then(function(u){
      var host = document.getElementById('aali-extras-host');
      if(!host){
        host = document.createElement('div');
        host.id = 'aali-extras-host';
        document.body.appendChild(host);
      }
      var old = document.getElementById('aaliv');
      if(old) old.remove();
      var tmp = document.createElement('div'); tmp.innerHTML = u.chip;
      host.appendChild(tmp.firstElementChild);
      wire('#aaliv', checkNow);

      var bh = document.getElementById('aalibanner');
      if(bh) bh.remove();
      if(u.banner){
        var bt = document.createElement('div'); bt.innerHTML = u.banner;
        document.body.appendChild(bt.firstElementChild);
        wire('#aalibuy', function(){ download().then(waitForStage).then(showDone); });
        wire('#aalilater', function(){ var n=document.getElementById('aalibanner'); if(n) n.remove(); });
      }
      if(box) openSettings();
    }).catch(function(){});
  }

  function waitForStage(){
    var tries = 0;
    return new Promise(function(resolve){
      var timer = setInterval(function(){
        tries++;
        api.update_status().then(function(s){
          var phase = ((s.job)||{}).phase;
          if(phase === 'staged' || phase === 'error' || tries > 240){
            clearInterval(timer); resolve(phase);
          }
        }).catch(function(){ clearInterval(timer); resolve('error'); });
      }, 1000);
    });
  }

  function checkNow(){
    return api.update_check(true).then(function(d){
      mount();
      if(d && d.ok && d.update_available){
        return download().then(waitForStage).then(showDone);
      }
      return d;
    }).catch(function(e){ return {ok:false, error:String(e)}; });
  }

  function openSettings(){
    closeBox();
    var t = document.createElement('div'); t.innerHTML = SETTINGS_HTML;
    box = t.firstElementChild;
    document.body.appendChild(box);
    wire('#aalivcheck', function(){ api.update_check(true).then(mount).then(closeBox); });
    wire('#aalivsave', function(){
      api.update_save(JSON.stringify({
        auto_check: document.getElementById('aalivauto').checked,
        channel: document.getElementById('aalivchan').value,
        hub_url: document.getElementById('aalivhub').value.trim()
      })).then(function(){ closeBox(); mount(); }).catch(closeBox);
    });
    wire('#aalivclose', closeBox);
  }

  function download(){ return api.update_download().catch(function(e){ return {ok:false,error:String(e)}; }); }

  function showDone(){
    api.update_ui().then(function(u){
      var t = document.createElement('div'); t.innerHTML = u.dialog;
      modal = t.firstElementChild;
      document.body.appendChild(modal);
      wire('#aalidonerestart', function(){
        api.update_activate().then(function(d){
          if(d && !d.ok) closeModal();
        });
      });
      wire('#aalidonelater', closeModal);
    });
  }

  var SETTINGS_HTML = '';
  api.update_ui().then(function(u){
    SETTINGS_HTML = u.settings;
    mount();
  });
  // The owner's launch-time auto-check toggle, honoured here rather than
  // forced: AALI_DESKTOP auto_check default ON, and off means off.
  api.update_status().then(function(s){
    if(s && s.can_update && s.config && s.config.auto_check){
      api.update_check(false).then(function(d){
        if(d && d.ok && d.update_available){ mount(); }
      }).catch(function(){});
    }
  });
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){ closeModal(); closeBox(); }
  });
  setInterval(function(){ if(window.__aaliUpdate) mount(); }, 5 * 60 * 1000);
})();
"""