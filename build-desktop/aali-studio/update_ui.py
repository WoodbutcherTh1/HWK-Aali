"""آلي ستوديو — the four update surfaces, rendered on the SERVER.

Why server-rendered HTML instead of JS string templates: every label, every
control id and the whole banner/dialog copy become assertable in pytest,
without a browser, in Arabic — which is exactly what ``tests/
test_studio_update_ui.py`` pins. The client (``web/app.js``) only wires ids to
``/api/update/*``.

The four surfaces the owner asked for:

1. the status bar chip ``v1.0.0 ✓`` (click → check),
2. the top banner ``🎉 إصدار جديد v1.1.0 متوفر — [تحديث الآن] [لاحقاً]``,
3. the Settings → «التحديثات» section (version, check, auto-check toggle,
   channel, editable hub URL, rollback, log path),
4. the post-download dialog ``تم تنزيل التحديث. [إعادة التشغيل الآن] [لاحقاً]``.

Every value that came from the HUB is HTML-escaped here: the version string
and the release notes are remote data, and a hub must never be able to inject
markup into the owner's IDE.
"""

from __future__ import annotations

import html
from typing import Any

import updater as studio_updater
import version as studio_version

CHANNEL_LABELS = {"stable": "مستقر", "beta": "تجريبي"}


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _checked(flag: Any) -> str:
    return " checked" if flag else ""


def _selected(current: str, value: str) -> str:
    return " selected" if current == value else ""


# ————— [1] the status-bar chip —————
def status_bar_html(info: dict[str, Any]) -> str:
    """``v1.0.0 ✓`` — bottom-right, click to check.

    The tick means 'checked and current'; a dot means 'an update is waiting';
    a dash means 'not checked yet' or 'updates unavailable here'. It never
    says ✓ for a build that could not verify anything.
    """
    version = studio_version.version_string()
    if not info.get("can_update"):
        mark, cls, title = "—", "off", str(info.get("reason") or "")
    elif info.get("update_available"):
        mark, cls, title = "●", "new", "تحديث جديد متوفر"
    elif info.get("checked"):
        mark, cls, title = "✓", "ok", "أنت على أحدث إصدار"
    else:
        mark, cls, title = "○", "idle", "اضغط للتحقق من التحديثات"
    return (
        f'<button id="update-status" class="update-status {cls}" '
        f'data-current="{esc(version)}" '
        f'title="{esc(title)} — اضغط للبحث عن تحديثات">'
        f'{esc(version)} {mark}</button>')


# ————— [2] the top banner —————
def banner_html(info: dict[str, Any]) -> str:
    """The update banner. Empty string when there is nothing to announce."""
    if not info.get("update_available"):
        return ""
    latest = esc(info.get("latest_version") or "")
    notes = str(info.get("release_notes_ar") or "").strip()
    if not notes:
        notes = str(info.get("release_notes_en") or "").strip()
    note_html = f'<div class="update-notes">{esc(notes[:600])}</div>' if notes else ""
    notice = str(info.get("notice") or "").strip()
    notice_html = (f'<div class="update-notice">{esc(notice)}</div>'
                   if notice else "")
    disabled = "" if info.get("auto_apply_allowed") else " disabled"
    return (
        '<div id="update-banner" class="update-banner" role="status">'
        '<div class="update-banner-text">'
        f'<b>🎉 إصدار جديد v{latest} متوفر</b>{notice_html}{note_html}'
        '</div>'
        '<div class="update-banner-actions">'
        f'<button id="btn-update-now" class="primary"{disabled}>تحديث الآن</button>'
        '<button id="btn-update-later" class="ghost">لاحقاً</button>'
        '</div></div>')


# ————— [3] the Settings → «التحديثات» section —————
def settings_section_html(info: dict[str, Any]) -> str:
    config = dict(info.get("config") or {})
    job = dict(info.get("job") or {})
    can = bool(info.get("can_update"))
    phase = str(job.get("phase") or "idle")
    job_text = {
        "checking": "جارٍ البحث…", "downloading": "جارٍ التنزيل والتحقق…",
        "staged": "تم التنزيل والتحقق — جاهز للتثبيت",
        "activating": "يعيد التشغيل…", "error": "فشل — راجع السجل",
    }.get(phase, "جاهز")
    rows = [
        ("الإصدار الحالي", esc(studio_version.version_string())),
        ("تاريخ البناء", esc(studio_version.BUILD_DATE)),
        ("المنصّة", esc(info.get("platform") or "")),
        ("مفتاح التحقق", "مثبّت ✓" if info.get("has_public_key")
         else "غير مثبّت — التحديثات معطّلة"),
        ("سجل التحديثات", f'<code dir="ltr">{esc(info.get("log_path"))}</code>'),
    ]
    if info.get("current_version_on_disk"):
        rows.append(("النسخة المثبّتة", esc(info["current_version_on_disk"])))
    if info.get("previous_version"):
        rows.append(("النسخة السابقة المحفوظة", esc(info["previous_version"])))
    body = "".join(f"<div class='kv'><span>{label}</span><b>{value}</b></div>"
                   for label, value in rows)
    options = "".join(
        f'<option value="{esc(key)}"{_selected(str(config.get("channel")), key)}>'
        f'{esc(label)}</option>'
        for key, label in CHANNEL_LABELS.items())
    disabled_attr = "" if can else " disabled"
    notice = str(info.get("reason") or info.get("notice") or "").strip()
    notice_html = f'<div class="update-warn">{esc(notice)}</div>' if notice else ""
    job_html = f'<div class="update-job" data-phase="{esc(phase)}">{esc(job_text)}</div>'
    return (
        '<section id="update-settings" class="dialog-section">'
        '<h3>التحديثات</h3>'
        f'{notice_html}{body}'
        '<label class="update-row"><span>التحقق التلقائي عند التشغيل</span>'
        f'<input id="update-auto" type="checkbox"{_checked(config.get("auto_check"))}>'
        '</label>'
        '<label class="update-row"><span>القناة</span>'
        f'<select id="update-channel"><option value="stable"{_selected(str(config.get("channel")), "stable")}>مستقر</option>'
        f'<option value="beta"{_selected(str(config.get("channel")), "beta")}>تجريبي</option></select></label>'
        f'<label class="update-row"><span>عنوان مركز التحديثات</span>'
        f'<input id="update-hub" dir="ltr" spellcheck="false" '
        f'value="{esc(config.get("hub_url"))}"></label>'
        f'{job_html}'
        '<div class="dialog-actions">'
        f'<button id="btn-update-check" class="ghost"{disabled_attr}>تحقّق من التحديثات</button>'
        f'<button id="btn-update-rollback" class="ghost danger-ghost"{disabled_attr}>استعد الإصدار السابق</button>'
        f'<button id="btn-update-save" class="primary"{disabled_attr}>حفظ</button>'
        '</div></section>')


# ————— [4] the post-download dialog —————
def downloaded_dialog_html(info: dict[str, Any]) -> str:
    """``تم تنزيل التحديث. [إعادة التشغيل الآن] [لاحقاً]``"""
    version = esc(info.get("latest_version") or
                  (info.get("job") or {}).get("version") or "")
    return (
        '<dialog id="update-done-dialog" class="dialog">'
        '<h2>تم تنزيل التحديث</h2>'
        f'<p>الإصدار v{version} نُزّل وتم التحقق من توقيعه.</p>'
        '<p class="note">التثبيت يحدث عند إعادة التشغيل. لو فشل الإقلاع '
        'مرتين، سيعود آلي ستوديو تلقائياً إلى الإصدار السابق.</p>'
        '<div class="dialog-actions">'
        '<button id="btn-update-later-2" class="ghost">لاحقاً</button>'
        '<button id="btn-update-restart" class="primary">إعادة التشغيل الآن</button>'
        '</div></dialog>')


# ————— the client wiring (one block, injected by app.js) —————
CLIENT_SCRIPT = """
/* آلي ستوديو — auto-update wiring (generated surface ids, see update_ui.py) */
async function updateStatus() {
  return api("/api/update/status");
}
async function updateMount() {
  const info = await updateStatus();
  state.update = info;
  const bar = document.getElementById("update-status-slot");
  if (bar) bar.innerHTML = await api("/api/update/ui/bar");
  const host = document.getElementById("update-banner-slot");
  if (host) {
    const html = await api("/api/update/ui/banner");
    host.innerHTML = html || "";
    host.hidden = !html;
    const now = document.getElementById("btn-update-now");
    if (now) now.onclick = () => updateApply().catch((e) => toast(e.message));
    const later = document.getElementById("btn-update-later");
    if (later) later.onclick = () => { host.hidden = true; };
  }
  wireUpdateSettings(info);
  if (info.update_available && info.auto_apply_allowed) return info;
  return info;
}
function wireUpdateSettings(info) {
  const bar = document.getElementById("update-status");
  if (bar) bar.onclick = () => updateCheck(true).catch((e) => toast(e.message));
  const check = document.getElementById("btn-update-check");
  if (check) check.onclick = () => updateCheck(true).catch((e) => toast(e.message));
  const save = document.getElementById("btn-update-save");
  if (save) save.onclick = () => updateSave().catch((e) => toast(e.message));
  const back = document.getElementById("btn-update-rollback");
  if (back) back.onclick = () => updateRollback().catch((e) => toast(e.message));
}
async function updateCheck(force) {
  const data = await api("/api/update/check", { method: "POST",
    body: { force: !!force } });
  if (!data.ok) { toast(data.error || "تعذر التحقق من التحديثات"); return data; }
  await updateMount();
  toast(data.update_available
    ? "🎉 " + data.latest_version + " متوفر"
    : "أنت على أحدث إصدار");
  return data;
}
async function updateDownload() {
  const data = await api("/api/update/download", { method: "POST" });
  if (!data.ok) { toast(data.error || "تعذر بدء التنزيل"); return data; }
  if (data.phase === "staged" || data.staged) return showUpdateDone();
  const timer = setInterval(async () => {
    const info = await updateStatus();
    const phase = (info.job || {}).phase;
    if (phase === "staged") { clearInterval(timer); showUpdateDone(info); }
    if (phase === "error") { clearInterval(timer); toast(info.job.error); }
  }, 1200);
  return data;
}
function showUpdateDone() {
  const holder = document.getElementById("update-dialog-slot");
  if (!holder) return;
  api("/api/update/ui/dialog").then((html) => {
    holder.innerHTML = html;
    const fresh = holder.firstElementChild;
    const restart = document.getElementById("btn-update-restart");
    if (restart) restart.onclick = () => updateActivate().catch((e) => toast(e.message));
    for (const id of ["btn-update-later", "btn-update-later-2"]) {
      const node = document.getElementById(id);
      if (node) node.onclick = () => node.closest("dialog").close();
    }
    if (fresh && fresh.showModal) fresh.showModal();
  });
}
async function updateActivate() {
  const data = await api("/api/update/activate", { method: "POST" });
  if (!data.ok) { toast(data.error || "تعذر التثبيت"); return data; }
  toast(data.message || "سيُعاد التشغيل");
  return data;
}
async function updateRollback() {
  const data = await api("/api/update/rollback", { method: "POST" });
  toast(data.ok ? (data.message || "استُعيد الإصدار السابق")
                : (data.error || "لا يوجد إصدار سابق"));
  return data;
}
async function updateSave() {
  const data = await api("/api/update/config", { method: "POST", body: {
    auto_check: document.getElementById("update-auto").checked,
    channel: document.getElementById("update-channel").value,
    hub_url: document.getElementById("update-hub").value.trim(),
  }});
  if (!data.ok) { toast(data.error || "تعذر الحفظ"); return data; }
  await updateMount();
  toast("حُفظت إعدادات التحديث");
  return data;
}
"""


def mount_payload() -> dict[str, str]:
    """Everything the client needs in one round-trip (tests read this)."""
    info = studio_updater.status()
    return {
        "status_bar": status_bar_html(info),
        "banner": banner_html(info),
        "settings": settings_section_html(info),
        "dialog": downloaded_dialog_html(info),
        "script": CLIENT_SCRIPT,
    }