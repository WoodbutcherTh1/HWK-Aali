"""Aali Node in-shell first-run config screen — the UI half of Q4.

Owner decision (Q4): first-run entry is BUILT. The daemon CLI can only take
--token from a flag/env/saved config; this screen is the SHELL's answer for
a machine with none: one window, typed once, persisted by
aali_node.node_config (owner-only JSON), then the real shell starts.

Security contract (the house rules, applied to a form):
- the token field is type=password, is NEVER prefilled with a real secret
  (a configured token shows the placeholder "<configured — leave empty to
  keep>" and an empty submission KEEPS it), and is never echoed back.
- prefill and keep-on-empty values come from the CALLER'S RESOLVED settings
  (flag > env > saved config), passed explicitly — the screen never reads
  the config file itself, so an edit session can never silently drop a
  CLI-provided value the form did not show.
- validation is server-side (Python) — the page's HTML constraint is a
  convenience, never the gate: NodeConfig + explicit checks decide.
- the saved file is the standard NodeConfig JSON (owner-only permissions);
  nothing here prints or logs secret values.
- pure helpers (build_config_page / validate_config_form) are headless-
  testable; webview is touched only inside run_config_screen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aali_node.node_config import (  # noqa: E402
    NodeConfig, NodeConfigError, default_config_path)

__all__ = ["ConfigScreenApi", "build_config_page", "run_config_screen",
           "validate_config_form"]

_KEEP_PLACEHOLDER = "<configured — leave empty to keep>"

_TITLE = "عقدة آلي — الإعداد الأول"

_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>عقدة آلي — الإعداد الأول</title>
<style>
  :root {
    --bg: #252523; --panel: #2d2d2a; --line: #3a3a36;
    --text: #e8e4d8; --dim: #9a958a; --gold: #d4a94e; --bad: #c96f5f;
  }
  * { box-sizing: border-box; margin: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: "Segoe UI", Tahoma, sans-serif; font-size: 14px;
    padding: 16px; user-select: none;
  }
  h1 { font-size: 16px; font-weight: 600; color: var(--gold); margin-bottom: 4px; }
  .sub { color: var(--dim); font-size: 12px; margin-bottom: 14px; }
  label { display: block; color: var(--dim); font-size: 12px; margin: 10px 0 4px; }
  input[type=text], input[type=password] {
    width: 100%; background: var(--panel); color: var(--text);
    border: 1px solid var(--line); border-radius: 8px; padding: 9px;
    font-family: inherit; font-size: 13px; direction: ltr; text-align: left;
  }
  input:focus { outline: none; border-color: var(--gold); }
  .check { display: flex; align-items: center; gap: 8px; margin-top: 12px;
           font-size: 12.5px; color: var(--text); }
  .check input { accent-color: var(--gold); }
  #err { color: var(--bad); font-size: 12px; min-height: 18px; margin-top: 10px;
         white-space: pre-wrap; }
  .btns { display: flex; gap: 8px; margin-top: 8px; }
  button { flex: 1; background: var(--panel); color: var(--text);
           border: 1px solid var(--line); border-radius: 10px; padding: 10px;
           font-family: inherit; font-size: 13px; cursor: pointer; }
  button.primary { border-color: var(--gold); color: var(--gold); }
  button.primary:hover { background: #3a3323; }
  button:hover { border-color: var(--dim); }
  .note { color: var(--dim); font-size: 11px; margin-top: 12px; line-height: 1.6; }
</style>
</head>
<body>
  <h1>عقدة آلي — الإعداد الأول</h1>
  <div class="sub">تُحفظ محليًا بصلاحيات المالك فقط؛ تُكتب مرة واحدة.</div>

  <label for="hub">عنوان المركز (Hub URL)</label>
  <input type="text" id="hub" placeholder="ws://127.0.0.1:8080" value="__HUB__">

  <label for="token">رمز الوصول (JWT)</label>
  <input type="password" id="token" placeholder="__TOKEN_PLACEHOLDER__" value="">

  <label for="workspace">مساحة العمل (المجلد الوحيد المسموح)</label>
  <input type="text" id="workspace" value="__WORKSPACE__">

  <label for="update_hub">مركز التحديثات (اختياري)</label>
  <input type="text" id="update_hub" value="__UPDATE_HUB__">

  <label for="update_key">مفتاح التحقق للتحديثات (اختياري)</label>
  <input type="password" id="update_key" placeholder="__UPDATE_KEY_PLACEHOLDER__" value="">

  <div class="check">
    <input type="checkbox" id="allow_commands" __ALLOW_COMMANDS__>
    <label for="allow_commands" style="margin:0">السماح بتنفيذ الأوامر (run_command)</label>
  </div>

  <div id="err"></div>
  <div class="btns">
    <button class="primary" onclick="save()">حفظ وتشغيل</button>
    <button onclick="cancel()">إلغاء</button>
  </div>
  <div class="note">
    الرمز (JWT) يأتي من تسجيل الدخول في المركز. يُحفظ في ملف التهيئة
    بصلاحيات المالك ولا يُعرض أبدًا بعد الحفظ.
  </div>
<script>
  function cancel() { pywebview.api.cancel(); }
  async function save() {
    const val = id => document.getElementById(id).value.trim();
    const form = {
      hub_url: val('hub'), token: val('token'),
      workspace: val('workspace'), update_hub: val('update_hub'),
      update_key: val('update_key'),
      allow_commands: document.getElementById('allow_commands').checked,
    };
    const res = JSON.parse(await pywebview.api.submit(JSON.stringify(form)));
    if (!res.ok) { document.getElementById('err').textContent = res.error; return; }
    document.getElementById('err').textContent = '';
  }
</script>
</body>
</html>
"""


def build_config_page(existing: "dict[str, Any] | None" = None) -> str:
    """Render the form, prefilling non-secret values ONLY.

    ``existing`` keys: hub_url, workspace, update_hub (plain values),
    has_token / has_update_key (existence booleans — a configured secret
    becomes the keep-on-empty placeholder, never a value), allow_commands.
    """
    existing = existing or {}
    page = _PAGE
    page = page.replace("__HUB__", str(existing.get("hub_url", "") or ""))
    page = page.replace("__WORKSPACE__", str(existing.get("workspace", "") or ""))
    page = page.replace("__UPDATE_HUB__", str(existing.get("update_hub", "") or ""))
    page = page.replace("__TOKEN_PLACEHOLDER__",
                        _KEEP_PLACEHOLDER if existing.get("has_token") else "")
    page = page.replace("__UPDATE_KEY_PLACEHOLDER__",
                        _KEEP_PLACEHOLDER if existing.get("has_update_key") else "")
    allow = bool(existing.get("allow_commands", True))
    page = page.replace("__ALLOW_COMMANDS__", "checked" if allow else "")
    return page


class _KeepSecrets:
    """Duck-typed keep-on-empty source (attrs .token / .update_key)."""

    def __init__(self, token: str = "", update_key: str = "") -> None:
        self.token = token
        self.update_key = update_key


def validate_config_form(form: dict[str, Any], *,
                         keep: "Any | None" = None,
                         default_workspace: str = "") -> NodeConfig:
    """Validate one submission into a NodeConfig (raises NodeConfigError).

    ``keep``: an object exposing ``.token`` / ``.update_key`` (a NodeConfig,
    or the caller's resolved CLI/env values). An EMPTY secret field means
    "keep" — the real secret is taken from ``keep``, never from the page.
    """
    if not isinstance(form, dict):
        raise NodeConfigError("form must be an object")
    hub_url = str(form.get("hub_url", "") or "").strip()
    token = str(form.get("token", "") or "").strip()
    workspace = str(form.get("workspace", "") or "").strip()
    update_hub = str(form.get("update_hub", "") or "").strip()
    update_key = str(form.get("update_key", "") or "").strip()
    allow_commands = form.get("allow_commands", True)
    if not isinstance(allow_commands, bool):
        raise NodeConfigError("allow_commands must be a boolean")

    if not hub_url:
        raise NodeConfigError("hub_url is required")
    if not hub_url.startswith(("ws://", "wss://")):
        raise NodeConfigError("hub_url must start with ws:// or wss://")
    if len(hub_url) > 2048:
        raise NodeConfigError("hub_url unreasonably long")
    if not token and keep is not None:
        token = str(getattr(keep, "token", "") or "")
    if not token:
        raise NodeConfigError("token is required")
    if len(token) > 4096:
        raise NodeConfigError("token unreasonably long")
    if not update_key and keep is not None:
        update_key = str(getattr(keep, "update_key", "") or "")
    if update_hub and not update_key:
        raise NodeConfigError(
            "update_hub needs update_key — refusing to check updates "
            "it could not verify")
    if not workspace:
        workspace = default_workspace
    if len(workspace) > 1024:
        raise NodeConfigError("workspace path unreasonably long")
    return NodeConfig({
        "hub_url": hub_url, "token": token, "workspace": workspace,
        "allow_commands": allow_commands,
        "update_hub": update_hub, "update_key": update_key,
    })


class ConfigScreenApi:
    """pywebview js_api bridge for the config screen (one submit path)."""

    def __init__(self, config_path: "str | Path | None" = None, *,
                 default_workspace: str = "",
                 prefill: "dict[str, Any] | None" = None,
                 keep_token: str = "", keep_update_key: str = "") -> None:
        self._path = Path(config_path) if config_path else default_config_path()
        self._default_workspace = default_workspace
        self._prefill = prefill or {}
        self._keep = _KeepSecrets(keep_token, keep_update_key)
        self.saved: NodeConfig | None = None
        self.cancelled = False
        self._window: Any = None

    def bind_window(self, window: Any) -> None:
        self._window = window

    def page(self) -> str:
        return build_config_page({
            "hub_url": self._prefill.get("hub_url", ""),
            "workspace": self._prefill.get("workspace", ""),
            "update_hub": self._prefill.get("update_hub", ""),
            "has_token": bool(self._keep.token),
            "has_update_key": bool(self._keep.update_key),
            "allow_commands": self._prefill.get("allow_commands", True),
        })

    def submit(self, form_json: str) -> str:
        """Validate + persist; returns {'ok': true} or {'ok': false, error}."""
        try:
            form = json.loads(form_json)
        except ValueError:
            return json.dumps({"ok": False, "error": "form is not valid JSON"})
        try:
            cfg = validate_config_form(
                form, keep=self._keep,
                default_workspace=self._default_workspace)
            path = cfg.save(self._path)
        except NodeConfigError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        self.saved = cfg
        print(f"aali_node: config saved ({path}) — owner-only permissions")
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
        return json.dumps({"ok": True})

    def cancel(self) -> None:
        self.cancelled = True
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass


def run_config_screen(config_path: "str | Path | None" = None, *,
                      default_workspace: str = "",
                      prefill: "dict[str, Any] | None" = None,
                      keep_token: str = "",
                      keep_update_key: str = "") -> "NodeConfig | None":
    """Show the config window; return the saved NodeConfig, or None.

    None means the user cancelled (or no GUI backend — the caller must
    fall back to the honest CLI error, never silently serve tokenless).
    """
    try:
        import webview
    except ImportError:
        print("aali_node: pywebview is not installed — the config screen "
              "needs it (pip install pywebview>=5.0), or use "
              "--save-config from the CLI", file=sys.stderr)
        return None
    api = ConfigScreenApi(config_path, default_workspace=default_workspace,
                          prefill=prefill, keep_token=keep_token,
                          keep_update_key=keep_update_key)
    window = webview.create_window(
        _TITLE, html=api.page(), js_api=api, width=460, height=640,
        min_size=(380, 520), background_color="#252523")
    api.bind_window(window)
    try:
        webview.start()
    except Exception as exc:
        print(f"aali_node: GUI unavailable ({type(exc).__name__}) — "
              "use --save-config from the CLI instead", file=sys.stderr)
        return None
    return api.saved
