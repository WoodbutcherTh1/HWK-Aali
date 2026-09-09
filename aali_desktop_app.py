"""آلي Desktop — standalone Windows client (single .exe, no install).

pywebview (WebView2) window around Aali's web UI + native extras:
- First run: pick the server (saved to %APPDATA%\\AaliDesktop\\url.txt).
- SHARE: one-click Cloudflare quick-tunnel (cloudflared bundled) → a public
  URL the owner can send to friends; friends open it and create a key.
- UPDATE: on start, asks the server /api/desktop-version — if the served
  build is newer, a banner offers the download.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

APP_VERSION = "1.0.3"

CFG_DIR = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), "AaliDesktop")
CFG_FILE = os.path.join(CFG_DIR, "url.txt")
DEFAULT_URL = "http://127.0.0.1:5055"
SERVER = ""  # resolved in main()


def _resource(name: str) -> str:
    """Path of a bundled resource (PyInstaller onefile -> sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _load_saved_url() -> str:
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip() or DEFAULT_URL
    except OSError:
        return DEFAULT_URL


def _save_url(url: str) -> None:
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as fh:
            fh.write(url)
    except OSError:
        pass


def _server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _pick_url() -> str:
    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        url = sys.argv[1].rstrip("/")
        _save_url(url)
        return url
    return _load_saved_url()


def _find_cloudflared() -> str:
    """Bundled copy first, then next to the exe, then scripts/ fallback."""
    here = os.path.dirname(os.path.abspath(__file__))
    exe_dir = (
        os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else here
    )
    candidates = [
        _resource("cloudflared.exe"),
        os.path.join(exe_dir, "cloudflared.exe"),
        os.path.join(here, "cloudflared.exe"),
        os.path.join(here, "scripts", "cloudflared.exe"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return ""


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _looks_like_repo(base: str) -> bool:
    return (os.path.isfile(os.path.join(base, "scripts", "start_all.bat"))
            and os.path.isfile(os.path.join(base, "file-agent", "app.py")))


def _find_repo() -> str:
    """Locate the Aali checkout that can boot the local server.

    Order: AALI_HOME env → remembered location (repo.txt) → the exe/source
    directory → common home/DRIVE locations. Found paths are remembered so
    later launches skip the search.
    """
    candidates = [os.environ.get("AALI_HOME", "")]
    try:
        with open(os.path.join(CFG_DIR, "repo.txt"), encoding="utf-8") as fh:
            candidates.append(fh.read().strip())
    except OSError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    exe_dir = (os.path.dirname(sys.executable)
               if getattr(sys, "frozen", False) else "")
    candidates.extend([os.getcwd(), here, exe_dir, os.path.dirname(here)])
    home = os.path.expanduser("~")
    for pat in ("OneDrive/Desktop/HWK-Aali*", "Desktop/HWK-Aali*",
                "Documents/HWK-Aali*", "HWK-Aali*"):
        candidates.extend(sorted(glob.glob(os.path.join(home, pat))))
    for drive in ("C:", "D:"):
        candidates.extend(sorted(glob.glob(drive + "/HWK-Aali*")))
        candidates.extend(sorted(glob.glob(drive + "/hwk-projects/HWK-Aali*")))
    for cand in candidates:
        if cand and _looks_like_repo(cand):
            try:
                os.makedirs(CFG_DIR, exist_ok=True)
                with open(os.path.join(CFG_DIR, "repo.txt"), "w",
                          encoding="utf-8") as fh:
                    fh.write(cand)
            except OSError:
                pass
            return cand
    return ""


def _ensure_local_server() -> None:
    """Auto-run scripts/start_all.bat when a local server is not up yet
    (owner request 2026-09-09): launching the app must bring Aali up on its
    own — no manual .bat step. Hidden window, no extra browser tab
    (AALI_NO_BROWSER=1), then we wait for /api/health."""
    if not _is_local(SERVER) or _server_ready(SERVER):
        return
    repo = _find_repo()
    if not repo:
        return
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    env = dict(os.environ, AALI_NO_BROWSER="1")
    try:
        subprocess.Popen(  # noqa: S603
            ["cmd", "/c", "start", "", "/min",
             os.path.join(repo, "scripts", "start_all.bat")],
            cwd=repo, env=env, creationflags=flags)
    except OSError:
        return
    deadline = time.time() + 120  # cold boot: venv python + Flask + n8n probe
    while time.time() < deadline:
        if _server_ready(SERVER):
            return
        time.sleep(1)


class Bridge:
    """Exposed to JS as window.pywebview.api — share + update extras."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._tunnel_url = ""
        self._share_error = ""

    # ---- share (one-click public URL) ---------------------------------
    def share_start(self) -> str:
        """Start the quick tunnel; returns 'started' | 'already' | error."""
        if self._proc and self._proc.poll() is None:
            return "already"
        if not _is_local(SERVER):
            self._share_error = "الخادم على جهاز آخر — شغّل المشاركة من جهاز الخادم نفسه"
            return "error:" + self._share_error
        cf = _find_cloudflared()
        if not cf:
            self._share_error = "cloudflared غير موجود في حزمة التطبيق"
            return "error:" + self._share_error
        self._tunnel_url = ""
        self._share_error = ""
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                [cf, "tunnel", "--url", SERVER.rstrip("/") + "/", "--no-autoupdate"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            self._share_error = str(exc)
            return "error:" + self._share_error
        threading.Thread(target=self._read_tunnel, daemon=True).start()
        return "started"

    def _read_tunnel(self) -> None:
        assert self._proc and self._proc.stdout
        pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        for line in self._proc.stdout:
            m = pat.search(line)
            if m:
                self._tunnel_url = m.group(0)
                break

    def share_url(self) -> str:
        """Poll from JS: '' = still starting, URL = ready, 'error:..' = failed."""
        if self._tunnel_url:
            return self._tunnel_url
        if self._proc and self._proc.poll() is not None:
            return "error:" + (self._share_error or "توقف النفق فوراً")
        return ""

    def share_stop(self) -> str:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._tunnel_url = ""
            return "stopped"
        return "idle"

    def share_copy(self, url: str) -> str:
        try:
            subprocess.run(  # noqa: S603
                ["clip"], input=url, check=True, text=True,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            return "copied"
        except Exception:  # noqa: BLE001
            return "failed"

    # ---- misc ----------------------------------------------------------
    def app_version(self) -> str:
        return APP_VERSION

    def open_external(self, url: str) -> str:
        if not re.match(r"^https?://", url or ""):
            return "bad-url"
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
        return "opened"


EXTRAS_JS = """
(function(){
  if (window.__aaliExtras) return; window.__aaliExtras = true;
  var api = window.pywebview && window.pywebview.api;
  if (!api) return;
  var st = document.createElement('style');
  st.textContent = [
    '.aali-extras{position:fixed;bottom:14px;inset-inline-start:14px;z-index:99998;',
    'display:flex;flex-direction:column;gap:8px;font-family:Tajawal,system-ui,sans-serif}',
    '.aali-pill{border:1px solid #2a2c35;background:#18181ce6;color:#f2f2f3;',
    'border-radius:999px;padding:8px 14px;font-size:12.5px;cursor:pointer;backdrop-filter:blur(6px)}',
    '.aali-pill:hover{border-color:rgba(232,179,75,.5)}',
    '.aali-update{border-color:rgba(232,179,75,.55);background:rgba(232,179,75,.14);color:#f5cf8a;font-weight:700}',
    '.aali-box{background:#18181c;border:1px solid #2a2c35;border-radius:14px;padding:12px 14px;',
    'max-width:330px;font-size:12.5px;line-height:1.8;color:#8f929c;direction:rtl}',
    '.aali-box b{color:#f5cf8a;direction:ltr;unicode-bidi:embed;word-break:break-all}',
    '.aali-btns{display:flex;gap:6px;margin-top:8px}',
    '.aali-btns button{flex:1;border:0;border-radius:9px;padding:7px;font:inherit;font-weight:700;cursor:pointer}',
    '.aali-copy{background:#e8b34b;color:#241a05}.aali-hide{background:#23242b;color:#f2f2f3}'
  ].join('');
  document.head.appendChild(st);
  var wrap = document.createElement('div'); wrap.className='aali-extras'; document.body.appendChild(wrap);

  function pill(label, cls, onclick){
    var b=document.createElement('button'); b.type='button'; b.className='aali-pill '+(cls||'');
    b.textContent=label; b.onclick=onclick; wrap.appendChild(b); return b;
  }

  // update banner (only when a newer build is served)
  fetch((localStorage.getItem('aali_api')||location.origin)+'/api/desktop-version')
    .then(function(r){return r.json()})
    .then(function(d){
      var served=(d&&d.version)||'';
      var local=api.app_version();
      function parts(v){return v.split('.').map(Number);}
      var p=parts(served), q=parts(local), newer=false;
      for(var i=0;i<3;i++){ if((p[i]||0)>(q[i]||0)){newer=true;break;} if((p[i]||0)<(q[i]||0))break; }
      if(newer && d.download_url){
        pill('⬆️ تحديث جديد لـ آلي Desktop — نزّله','aali-update',function(){
          api.open_external((localStorage.getItem('aali_api')||location.origin)+d.download_url);
        });
      }
    }).catch(function(){});

  // share button
  var box=null;
  function showBox(){
    if(box) box.remove();
    box=document.createElement('div'); box.className='aali-box';
    box.innerHTML='<b>جارٍ إنشاء الرابط العام…</b>';
    wrap.appendChild(box);
    var tries=0;
    var t=setInterval(function(){
      api.share_url().then(function(u){
        tries++;
        if(u && u.indexOf('https://')===0){
          clearInterval(t);
          box.innerHTML='<b>رابطك العام جاهز — أرسله لأي شخص:</b><br><b>'+u+'</b>'+
            '<div class="aali-btns"><button class="aali-copy" id="aalicp">نسخ</button>'+
            '<button class="aali-hide" id="aalihr">إخفاء</button></div>';
          document.getElementById('aalicp').onclick=function(){
            api.share_copy(u); box.querySelector('#aalicp').textContent='تم النسخ ✓';
          };
          document.getElementById('aalihr').onclick=function(){ box.remove(); box=null; };
        } else if(u && u.indexOf('error:')===0){
          clearInterval(t);
          box.innerHTML='تعذّر إنشاء الرابط: '+u.slice(6);
        } else if(tries>30){
          clearInterval(t);
          box.innerHTML='انتهت المهلة — تأكد من الاتصال ثم أعد المحاولة.';
        }
      });
    }, 1000);
  }
  pill('🔗 شارك آلي (رابط عام)','',function(){
    if(box){ box.remove(); box=null; return; }
    api.share_start().then(function(res){
      showBox();
      if(res==='error:cloudflared غير موجود في حزمة التطبيق'){}
    });
  });

  // platform pills — connect Aali as a model on n8n / OpenRouter-style tools /
  // Hugging Face (all speak the OpenAI-compatible /v1 endpoint).
  var base=(localStorage.getItem('aali_api')||location.origin).replace(/\\/$/,'');
  function platformBox(title, lines, copyText){
    if(box) box.remove();
    box=document.createElement('div'); box.className='aali-box';
    box.innerHTML='<b>'+title+'</b><br>'+lines.join('<br>')+
      '<div class="aali-btns"><button class="aali-copy" id="aalicp">نسخ الإعدادات</button>'+
      '<button class="aali-hide" id="aalihr">إخفاء</button></div>';
    wrap.appendChild(box);
    document.getElementById('aalicp').onclick=function(){
      api.share_copy(copyText); this.textContent='تم النسخ ✓';
    };
    document.getElementById('aalihr').onclick=function(){ box.remove(); box=null; };
  }
  pill('🧩 اربط آلي مع n8n','',function(){
    var lines=[
      '1. في n8n: أضف عقدة HTTP Request.',
      '2. Method POST → '+base+'/v1/chat/completions',
      '3. Header Authorization: Bearer <مفتاح آلي>',
      '4. Body: {"model":"aali","messages":[{"role":"user","content":"..."}]}',
      'مفتاحك: من /admin أو /signup (نفس مفتاح الويب).'
    ];
    platformBox('🧩 n8n', lines,
      'POST '+base+'/v1/chat/completions\nAuthorization: Bearer <Aali key>\n{"model":"aali","messages":[{"role":"user","content":""}]}');
  });
  pill('🔄 اربط آلي مع OpenRouter','',function(){
    var lines=[
      'أي عميل يتكلم OpenAI (مثل أدوات OpenRouter،\nLibreChat، Cline…) اضبط:'
    ];
    platformBox('🔄 OpenRouter-style', lines,
      'Base URL: '+base+'/v1\nAPI Key: <Aali key>\nModel: aali');
  });
  pill('🤗 اربط آلي مع Hugging Face','',function(){
    var lines=[
      'في أي أداة تدعم Inference/OpenAI:','Base URL → '+base+'/v1','Model → aali'
    ];
    platformBox('🤗 Hugging Face', lines,
      'Base URL: '+base+'/v1\nModel: aali\nAPI Key: <Aali key>');
  });
})();
"""


def _wait_and_inject(window) -> None:
    deadline = time.time() + 90  # covers a cold auto-boot of the server
    while time.time() < deadline and not _server_ready(SERVER):
        time.sleep(1)
    try:
        window.evaluate_js(
            "localStorage.setItem('aali_api', '" + SERVER + "');"
            "if (window.aaliPing) window.aaliPing();"
            "location.href='" + SERVER + "/ui/';"
        )
        time.sleep(2)
        window.evaluate_js(EXTRAS_JS)
    except Exception:  # noqa: BLE001
        pass


def _reinject_on_load(window) -> None:
    """Re-inject extras when the SPA reloads (e.g. after new chat)."""
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(5)
        try:
            window.evaluate_js(
                "if(!window.__aaliExtras && window.pywebview && document.body){"
                + EXTRAS_JS.replace("\n", " ")
                + "}"
            )
        except Exception:  # noqa: BLE001
            break


def main() -> None:
    global SERVER
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    SERVER = _pick_url()

    if _is_local(SERVER):
        threading.Thread(target=_ensure_local_server, daemon=True).start()

    import webview

    window = webview.create_window(
        "آلي — Desktop",
        SERVER + "/ui/",
        width=1180,
        height=840,
        min_size=(760, 560),
        background_color="#121214",
        js_api=Bridge(),
    )
    threading.Thread(target=_wait_and_inject, args=(window,), daemon=True).start()
    threading.Thread(target=_reinject_on_load, args=(window,), daemon=True).start()
    webview.start(http_server=False)


if __name__ == "__main__":
    main()
