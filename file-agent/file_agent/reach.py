"""آلي ريتش — Aali Reach: read a PUBLIC link, read-only, with a hard fence.

A URL reader sitting next to ``run_command`` is the most dangerous tool in this
repo, so the fence IS the feature and the fetching is the easy part:

* **SSRF fence** — http/https only, no credentials in the URL, and every
  resolved A/AAAA record must be a public address. Loopback, RFC1918,
  link-local, CGNAT, multicast and reserved ranges are refused, and the check
  is repeated for every redirect hop (a public host can answer 302 ->
  ``http://127.0.0.1:5055/``, which is the entire attack).
* **No ambient authority** — no cookies, no storage, no Authorization header,
  and none of the owner's keys are ever attached to a fetch.
* **Read-only at the wire** — only GET and HEAD are ever issued. The optional
  browser bridge (``scripts/reach_fetch.py``) enforces the same rule at the
  Playwright route layer so page JavaScript cannot turn a read into a write.
* **Bounded** — bytes, extracted text, redirects and wall-clock time all have
  hard caps, and every cap that bit is reported in the result.
* **Content-free audit** — host, status, byte count, engine and a text hash.
  Never the text, never the URL path, never a query string.

Everything here is stdlib. Playwright/Camoufox live in their own venv and are
reached through a subprocess bridge, because AGENTS.md forbids heavy deps in
the training venv.

    from file_agent import reach
    result = reach.read_link("https://example.com/post")
"""

from __future__ import annotations

import hashlib
import html.parser
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ————— hard caps (every one is reported when it bites) —————
MAX_URL_CHARS = 2_000
MAX_REDIRECTS = 4
MAX_BYTES = 4_000_000
MAX_TEXT_CHARS = 20_000
DEFAULT_TIMEOUT = 30
BRIDGE_TIMEOUT_EXTRA = 90

#: The browser bridge lives here and is the ONLY thing that may touch a
#: rendered page. It is a subprocess on purpose: playwright never gets imported
#: by the agent process, so a page's JavaScript can never reach our runtime.
BRIDGE_RELATIVE = ("scripts", "reach_fetch.py")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

AUDIT_PATH = Path(os.getenv("AALI_REACH_AUDIT",
                            "D:/hwk-data/reach_audit.jsonl"))


class ReachError(Exception):
    """Expected, owner-facing refusal. The message is safe to show."""


# ————— 1. the URL fence —————

def _is_public_ip(raw: str) -> tuple[bool, str]:
    literal = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        return False, f"not an IP: {raw}"
    if ip.is_unspecified:
        return False, "unspecified (0.0.0.0)"
    if ip.is_loopback:
        return False, "loopback"
    if ip.is_link_local:
        # Checked BEFORE is_private: 169.254.0.0/16 is link-local, and calling
        # it "RFC1918" would be a wrong label on the exact address that leaks
        # cloud metadata.
        return False, "link-local"
    if ip.is_private:
        return False, "private (RFC1918)"
    if ip.is_multicast:
        return False, "multicast"
    if ip.is_reserved:
        return False, "reserved"
    # 100.64.0.0/10 is "shared address space" — neither public nor RFC1918, but
    # it routes inside someone's network. is_private covers it on 3.13+, but
    # not on every runtime, so it is named explicitly.
    if raw.startswith("100.") and 64 <= int(raw.split(".")[1]) <= 127:
        return False, "CGNAT shared address space"
    return True, ""


def validate_url(url: str) -> str:
    """Return the normalized URL or raise ReachError. Never fetch anything."""
    text = str(url or "").strip()
    if not text:
        raise ReachError("الرابط مطلوب")
    if len(text) > MAX_URL_CHARS:
        raise ReachError("الرابط طويل جداً")
    if text != text.strip() or any(ch.isspace() for ch in text):
        raise ReachError("الرابط يحتوي مسافات")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ReachError("مدعومات العنوان: http:// أو https:// فقط")
    if not parsed.netloc:
        raise ReachError("الرابط بلا عنوان")
    if parsed.username or parsed.password:
        # A URL with inline credentials is either a phishing trick or an
        # attempt to make the fetch authenticate as someone.
        raise ReachError("لا يُقبل رابط يحمل اسم مستخدم أو كلمة مرور")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ReachError("الرابط بلا مضيف")
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise ReachError("localhost is not a public link")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        raise ReachError("رقم المنفذ غير صالح") from None
    if not (0 < port < 65536):
        raise ReachError("رقم المنفذ غير صالح")
    # Rebuild the authority from the already-parsed pieces so the host is
    # lowercased and the default port is never invented: "HTTP://Example.COM"
    # and "http://example.com" must produce the same key in the audit log.
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    netloc = host if port == default_port else f"{host}:{port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def resolve_public_ips(host: str, port: int) -> list[str]:
    """Every address the host resolves to, or raise. ALL of them must be public.

    A name that resolves to one public and one private address is refused:
    DNS rebinding turns the public answer into the private one on the next
    lookup, and the safe answer is to never fetch such a name at all.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ReachError(f"cannot resolve {host}: {exc.strerror or exc}") from None
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise ReachError(f"no address for {host}")
    for raw in addresses:
        ok, why = _is_public_ip(raw)
        if not ok:
            raise ReachError(
                f"{host} resolves to a {why} address ({raw}) — refusing. "
                f"آلي ريتش يقرأ الروابط العامة فقط.")
    return addresses


def guard_host(host: str, port: int) -> list[str]:
    """Public-IP literals skip DNS; everything else is resolved and checked."""
    literal = host
    if literal.startswith("[") and literal.endswith("]"):
        literal = literal[1:-1]
    try:
        ipaddress.ip_address(literal)
    except ValueError:
        return resolve_public_ips(host, port)
    ok, why = _is_public_ip(literal)
    if not ok:
        raise ReachError(f"{host} is a {why} address — refusing. "
                         f"آلي ريتش يقرأ الروابط العامة فقط.")
    return [literal]


# ————— 2. the fetch (static, stdlib) —————

@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes
    content_type: str
    elapsed_ms: int
    redirects: int = 0
    engine: str = "static"
    caps: list[str] = field(default_factory=list)


def _class(name: str) -> str:
    lowered = name.lower()
    if "text/html" in lowered or "application/xhtml" in lowered:
        return "html"
    if lowered.startswith("text/"):
        return "text"
    if "json" in lowered:
        return "json"
    return "binary"


def _decode(data: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1).strip().strip('"').lower()
    for candidate in (charset, "utf-8", "windows-1256", "latin-1"):
        if not candidate:
            continue
        try:
            return data.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def _open(url: str, timeout: int, method: str = "GET"):
    """Open with a hand-rolled redirect loop so every hop is re-guarded.

    urllib's own redirect handler follows 302s BEFORE we can look at the new
    host, which is precisely the hole. So redirects are disabled here and the
    loop is ours.
    """
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        guard_host(host, port)
        request = urllib.request.Request(current, method=method, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar,en;q=0.8",
            # No cookies, ever. One static read must not become a session.
            "Cookie": "",
        })
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                if hop >= MAX_REDIRECTS:
                    raise ReachError("too many redirects") from None
                location = urllib.parse.urljoin(current, exc.headers["Location"])
                exc.close()
                current = validate_url(location)
                continue
            body = b""
            try:
                body = exc.read(MAX_BYTES)
            except Exception:  # noqa: BLE001
                pass
            raise ReachError(f"the site answered HTTP {exc.code}") from None
        with response:
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > MAX_BYTES:
                raise ReachError(
                    f"the page is {int(declared) // 1024} KB — over the "
                    f"{MAX_BYTES // 1024} KB cap")
            body = response.read(MAX_BYTES + 1)
            caps = ["bytes_capped"] if len(body) > MAX_BYTES else []
            body = body[:MAX_BYTES]
            return FetchResult(url=url, final_url=current, status=response.status,
                               body=body,
                               content_type=response.headers.get("Content-Type", ""),
                               elapsed_ms=0, redirects=hop, caps=caps)
    raise ReachError("too many redirects")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand 3xx back to us instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


# ————— 3. the extractor —————

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head", "canvas"}
_BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2",
               "h3", "h4", "h5", "h6", "blockquote", "pre"}


class _Extractor(html.parser.HTMLParser):
    """HTML → readable text plus the metadata a reader actually wants.

    Deliberately not a full readability port: the goal is honest text with no
    invention, and a 300-line parser that guesses is worse than one that
    extracts.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.metas: dict[str, str] = {}
        self.links: list[str] = []
        self.images: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key and attributes.get("content"):
                self.metas.setdefault(key, attributes["content"].strip())
        elif tag == "a":
            self._href = attributes.get("href") or ""
        elif tag == "img":
            src = attributes.get("src") or attributes.get("data-src") or ""
            if src:
                self.images.append(src)
            alt = (attributes.get("alt") or "").strip()
            if alt:
                self.parts.append("🖼 " + alt)
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a":
            if self._href and self._href.startswith(("http://", "https://")):
                self.links.append(self._href)
            self._href = None
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip_depth:
            return
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)

    def result(self) -> str:
        text = " ".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*", "\n\n", text)
        return text.strip()


def _first(metas: dict[str, str], *keys: str) -> str:
    for key in keys:
        if metas.get(key):
            return metas[key].strip()
    return ""


def extract(fetched: FetchResult) -> dict[str, Any]:
    """Turn a response into the honest record the agent will quote."""
    kind = _class(fetched.content_type)
    out: dict[str, Any] = {
        "url": fetched.final_url,
        "requested_url": fetched.url,
        "status": fetched.status,
        "engine": fetched.engine,
        "content_type": fetched.content_type or kind,
        "bytes": len(fetched.body),
        "redirects": fetched.redirects,
        "caps": list(fetched.caps),
        "title": "",
        "author": "",
        "published": "",
        "site": "",
        "description": "",
        "text": "",
        "links": [],
        "images": [],
    }
    if kind == "binary":
        out["caps"] = out["caps"] + ["binary_skipped"]
        return out
    if kind == "json":
        out["text"] = _decode(fetched.body, fetched.content_type)[:MAX_TEXT_CHARS]
        out["caps"] = out["caps"] + (["text_capped"]
                                     if len(out["text"]) >= MAX_TEXT_CHARS else [])
        return out
    parser = _Extractor()
    try:
        parser.feed(_decode(fetched.body, fetched.content_type))
    except Exception as exc:  # noqa: BLE001 - a malformed page is still a page
        out["caps"] = out["caps"] + [f"parse_degraded:{type(exc).__name__}"]
    metas = parser.metas
    # og:title wins over <title>: it is the publisher's own canonical title for
    # the CONTENT, while <title> is usually the same string plus a site suffix.
    out["title"] = _first(metas, "og:title", "twitter:title") or parser.title
    out["author"] = _first(metas, "author", "article:author", "og:article:author",
                           "twitter:creator")
    out["published"] = _first(metas, "article:published_time", "og:updated_time",
                              "datepublished", "twitter:data")
    out["site"] = _first(metas, "og:site_name")
    out["description"] = _first(metas, "description", "og:description")
    text = parser.result()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        out["caps"] = out["caps"] + ["text_capped"]
    out["text"] = text
    out["links"] = parser.links[:40]
    out["images"] = parser.images[:20]
    return out


# ————— 4. the browser bridge (Playwright / Camoufox, own venv) —————

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bridge_path() -> Path:
    """scripts/reach_fetch.py, or None when this is a frozen build without it."""
    candidate = _repo_root().joinpath(*BRIDGE_RELATIVE)
    return candidate if candidate.is_file() else None


def bridge_env() -> dict[str, str]:
    """Prefer the dedicated reach venv; never fall back to the training venv."""
    env = dict(os.environ)
    venv = os.getenv("AALI_REACH_VENV", "D:/hwk-tools/reach-venv")
    for candidate in (Path(venv) / "Scripts" / "python.exe",
                      Path(venv) / "bin" / "python"):
        if candidate.is_file():
            env["PYTHON"] = str(candidate)
            break
    env.pop("PYTHONPATH", None)  # entry-point tripwire: no borrowed imports
    return env


def fetch_rendered(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run the bridge in its own venv. Honest failure when it is not installed."""
    script = bridge_path()
    if script is None:
        raise ReachError("the browser bridge is not in this build")
    env = bridge_env()
    interpreter = env.get("PYTHON")
    if not interpreter:
        raise ReachError(
            "the reading venv is not installed — run scripts\\setup_reach.bat, "
            "or read the link without the browser (engine=static)")
    try:
        result = subprocess.run(
            [interpreter, str(script), "--url", url,
             "--timeout", str(timeout),
             "--engine", os.getenv("AALI_REACH_ENGINE", "auto")],
            capture_output=True, text=True, timeout=timeout + BRIDGE_TIMEOUT_EXTRA,
            env=env)
    except subprocess.TimeoutExpired:
        raise ReachError(f"the browser did not answer within {timeout}s") from None
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-1:] or ["no detail"]
        raise ReachError("the browser bridge failed: " + tail[0][:200])
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        raise ReachError("the browser bridge returned something unreadable") from None


# ————— 5. the public entry point —————

def read_link(url: str, engine: str = "auto",
              timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Read a public link. Read-only, bounded, audited.

    ``engine``: ``static`` (stdlib only), ``browser`` (Playwright/Camoufox),
    or ``auto`` (static first, browser only if the static read came back thin).
    """
    started = time.time()
    safe_url = validate_url(url)
    record: dict[str, Any] = {"ok": False, "url": safe_url, "engine": "static",
                              "attempts": []}
    result: dict[str, Any] | None = None
    if engine in ("static", "auto"):
        try:
            fetched = _open(safe_url, timeout)
            fetched.elapsed_ms = int((time.time() - started) * 1000)
            result = extract(fetched)
            record["attempts"].append({"engine": "static", "ok": True})
        except ReachError as exc:
            record["attempts"].append({"engine": "static", "ok": False,
                                       "error": str(exc)})
            if engine == "static":
                record["error"] = str(exc)
                audit(record, result=None)
                return record
    thin = result is None or len(result.get("text", "")) < 200
    if engine == "browser" or (engine == "auto" and thin):
        try:
            rendered = fetch_rendered(safe_url, timeout)
            record["attempts"].append({"engine": rendered.get("engine", "browser"),
                                       "ok": bool(rendered.get("ok"))})
            if rendered.get("ok") and rendered.get("text"):
                rendered["engine"] = rendered.get("engine", "browser")
                result = rendered
            elif result is None:
                record["error"] = rendered.get("error") or "the browser read nothing"
        except ReachError as exc:
            record["attempts"].append({"engine": "browser", "ok": False,
                                       "error": str(exc)})
            if result is None:
                record["error"] = str(exc)
    if result is None:
        record.setdefault("error", "nothing could be read from this link")
        record["elapsed_ms"] = int((time.time() - started) * 1000)
        audit(record, result=None)
        return record
    record["ok"] = True
    record["result"] = result
    record["elapsed_ms"] = int((time.time() - started) * 1000)
    audit(record, result=result)
    return record


# ————— 6. the audit (content-free, always) —————

def audit(record: dict[str, Any], result: dict[str, Any] | None) -> None:
    """Host, verdict, bytes, hash. Never the text, never the path, never a query.

    A URL can carry a token in its query string, so only the HOST is recorded.
    """
    try:
        host = (urllib.parse.urlsplit(record.get("url") or "").hostname or "?").lower()
        text = (result or {}).get("text") or ""
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": host,
            "ok": bool(record.get("ok")),
            "engine": (result or {}).get("engine") or record.get("engine"),
            "status": (result or {}).get("status"),
            "bytes": (result or {}).get("bytes"),
            "chars": len(text),
            "text_sha16": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "error": str(record.get("error") or "")[:120] or None,
        }
        path = AUDIT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - auditing must never break a read
        pass


def summary(record: dict[str, Any]) -> str:
    """The short Arabic line the agent shows the owner."""
    if not record.get("ok"):
        return "تعذّرت قراءة الرابط: " + str(record.get("error") or "سبب غير معروف")
    result = record.get("result") or {}
    head = result.get("title") or result.get("url") or "الرابط"
    bits = [f"📄 {head}"]
    if result.get("author"):
        bits.append(f"✍️ {result['author']}")
    if result.get("published"):
        bits.append(f"🗓 {result['published'][:10]}")
    chars = len(result.get("text") or "")
    bits.append(f"{chars} حرف · {result.get('engine')} · {record.get('elapsed_ms')}ms")
    if result.get("caps"):
        bits.append("· " + ", ".join(result["caps"]))
    return "\n".join(bits)
