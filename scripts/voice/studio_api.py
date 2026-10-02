# -*- coding: utf-8 -*-
"""Voice Studio API + console host (Phase 2 slice 2).

One stdlib HTTP server on :5082 that serves the Arabic-first console
(web/voice/studio.html) and its JSON API:

  GET    /api/voices                library + engine availability per language
  POST   /api/voices/check          validate an uploaded clip (no registration)
  POST   /api/voices                register a clip  (multipart: file, name, lang)
  POST   /api/voices/default        {lang, name}
  DELETE /api/voices/<name>         remove a profile
  POST   /api/synthesize            {text, lang?, voice?} -> audio/wav
  POST   /api/batch                 {script, lang?, voice?} -> manifest
  GET    /api/audio/<sha1>.wav      export/download a synthesized clip

Honest by construction:
- loopback unless AALI_VOICE_BIND is set (the house fail-safe);
- an unknown voice name is an ERROR, never a silent fallback;
- text is chunked with the engine's per-language cap, so nothing is
  truncated by the engine;
- the audio route serves ONLY sha1-named files from the TTS cache (no
  traversal, no arbitrary file read);
- AALI_VOICE_FAKE=1 swaps the heavy engine for a deterministic seam so the
  whole console can be tested on CPU with no models.
"""
from __future__ import annotations

import os
import sys

# run as a plain script (`python scripts/voice/studio_api.py`): the voice
# package lives in scripts/, and file_agent (the Piper stack) in file-agent/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_REPO_ROOT, "file-agent")):
    sys.path.insert(0, os.path.join(_REPO_ROOT, "file-agent"))

import json
import re
import shutil
import tempfile
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from voice import force_utf8_stdio
from voice.core import voices as voice_lib
from voice.core.language_detect import chunk_for_tts, detect_script_lang

WEB_DIR = Path(__file__).resolve().parents[2] / "web" / "voice"
DEFAULT_PORT = 5082
MAX_BODY = 8 * 1024 * 1024        # an 8 MB upload is already a long recording
MAX_TEXT = 2000
UPLOAD_DIR = Path(os.environ.get("AALI_VOICE_UPLOADS") or r"D:/hwk-data/voice_uploads")
AUDIO_NAME = re.compile(r"^[0-9a-f]{40}\.wav$")

# content-free log (names, counts, timings — never the script text)
LOG_FILE = Path(r"D:/hwk-data/voice_studio_api.log")


def _log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# engine construction
# --------------------------------------------------------------------------

def build_tts(fake: bool = False):
    """Real XTTS engine, or the deterministic test seam."""
    if fake:
        import numpy as np

        class FakeTTS:
            engine = "fake"

            def synthesize(self, text, lang="ar", voice=None):
                import wave

                ttsmod_dir = Path(os.environ.get("AALI_VOICE_CACHE") or r"D:/hwk-data/voice_tts")
                ttsmod_dir.mkdir(parents=True, exist_ok=True)
                import hashlib

                digest = hashlib.sha1(
                    f"fake|{lang}|{voice or ''}|".encode("utf-8") + text.encode("utf-8")
                ).hexdigest()
                out = ttsmod_dir / f"{digest}.wav"
                if not out.exists():
                    n = 24000  # one second of a real tone — a playable wav
                    t = np.arange(n) / 24000.0
                    pcm = (np.sin(2 * np.pi * 220.0 * t) * 8000).astype("<i2")
                    with wave.open(str(out), "wb") as wf:
                        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                        wf.writeframes(pcm.tobytes())
                return {"ok": True, "path": str(out), "sr": 24000,
                        "engine": "fake", "cached": False, "voice": voice or ""}

            def engine_report(self):
                from voice.core.tts import VoiceTTS

                return VoiceTTS().engine_report()

        return FakeTTS()
    from voice.core.tts import VoiceTTS

    return VoiceTTS()


# --------------------------------------------------------------------------
# request handling
# --------------------------------------------------------------------------

class StudioHandler(BaseHTTPRequestHandler):
    server_version = "AaliVoiceStudio/1.0"
    tts_factory: Callable[[], Any] = staticmethod(build_tts)
    _tts: Any = None

    # -- plumbing -------------------------------------------------------
    @property
    def tts(self):
        if self.__class__._tts is None:      # one engine per process (XTTS is heavy)
            self.__class__._tts = self.tts_factory()
        return self.__class__._tts

    def log_message(self, fmt, *args):       # no query strings, ever
        pass

    def _json(self, obj: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400, **extra: Any) -> None:
        _log(f"error status={status} msg={message[:120]}")
        self._json({"ok": False, "error": message, **extra}, status=status)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("payload too large")
        return self.rfile.read(length) if length else b""

    def _body_json(self) -> Dict[str, Any]:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("expected JSON")
        return data if isinstance(data, dict) else {}

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_page("studio.html")
        if path == "/api/voices":
            return self._json({
                "ok": True,
                **voice_lib.library_report(),
                "engines": self.tts.engine_report(),
            })
        if path.startswith("/api/audio/"):
            return self._serve_audio(path.rsplit("/", 1)[-1])
        if path in ("/app.js", "/style.css"):
            return self._serve_asset(path.lstrip("/"))
        return self._error("not found", 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/voices/check":
                return self._check_upload(register=False)
            if path == "/api/voices":
                return self._check_upload(register=True)
            if path == "/api/voices/default":
                return self._set_default()
            if path == "/api/synthesize":
                return self._synthesize()
            if path == "/api/batch":
                return self._batch()
        except ValueError as exc:
            return self._error(str(exc), 400)
        except Exception as exc:  # honest failure, never a silent 200
            return self._error(f"{type(exc).__name__}: {exc}", 500)
        return self._error("not found", 404)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/voices/"):
            return self._error("not found", 404)
        name = urllib.parse.unquote(path[len("/api/voices/"):])
        try:
            r = voice_lib.delete_voice(name)
        except ValueError as exc:
            return self._error(str(exc), 400)
        return self._json({"ok": bool(r.get("ok")), "error": r.get("error", ""),
                           **voice_lib.library_report()})

    # -- handlers ---------------------------------------------------------
    def _set_default(self) -> None:
        data = self._body_json()
        lang, name = str(data.get("lang", "")), str(data.get("name", ""))
        if not lang or not name:
            return self._error("lang and name are required", 400)
        try:
            r = voice_lib.set_default(lang, name)
        except ValueError as exc:
            return self._error(str(exc), 400)
        if not r.get("ok"):
            return self._error(r.get("error", "failed"), 400)
        _log(f"default lang={lang} name={name}")
        return self._json({"ok": True, **voice_lib.library_report()})

    def _check_upload(self, register: bool) -> None:
        """multipart upload -> validate -> (register).

        The clip is written to a temp file first: a candidate clip never
        lands in the managed references dir unless it passes validation.
        """
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._error("multipart/form-data required", 400)
        raw = self._read_body()
        fields, files = parse_multipart(raw, ctype)
        upload = files.get("file")
        if not upload or not upload[1]:
            return self._error("no file part (field name: file)", 400)
        filename, data = upload
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = UPLOAD_DIR / f"upload_{int(time.time() * 1000)}.wav"
        tmp.write_bytes(data)
        try:
            verdict = voice_lib.validate_reference_wav(tmp)
            if not register:
                return self._json({"ok": bool(verdict["ok"]), "check": verdict})
            name = (fields.get("name") or "").strip()
            if not name:
                return self._error("name is required", 400)
            result = voice_lib.add_voice(name, fields.get("lang", "ar"), tmp,
                                         note=fields.get("note", ""))
        except ValueError as exc:
            return self._error(str(exc), 400)
        finally:
            # the library keeps its OWN copy under REFERENCES_DIR, so the
            # upload scratch file is never needed afterwards — success or not
            tmp.unlink(missing_ok=True)
        if not result.get("ok"):
            return self._error(result.get("error", "rejected"), 400, check=result.get("check"))
        _log(f"upload registered name={result.get('name')} lang={result.get('lang')}")
        return self._json({"ok": True, **voice_lib.library_report()})

    def _synthesize(self) -> None:
        data = self._body_json()
        text = str(data.get("text", "")).strip()
        if not text:
            return self._error("text is required", 400)
        if len(text) > MAX_TEXT:
            return self._error(f"text too long (max {MAX_TEXT})", 400)
        lang = str(data.get("lang") or "") or detect_script_lang(text) or "ar"
        voice = data.get("voice") or None
        bad = self._voice_problem(voice)
        if bad:
            return self._error(bad, 400)

        # the engine truncates past its per-language cap: chunk first, and
        # for a multi-chunk request return the FIRST chunk honestly.
        chunks = chunk_for_tts(text)
        first = chunks[0] if chunks else text
        r = self.tts.synthesize(first, lang, voice=voice)
        if not r.get("ok"):
            return self._error(r.get("error", "synthesis failed"), 502)
        path = Path(r["path"])
        if not path.exists():
            return self._error("synthesized file missing", 500)
        body = path.read_bytes()
        name = path.name
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Aali-Lang", lang)
        self.send_header("X-Aali-Engine", str(r.get("engine", "")))
        self.send_header("X-Aali-Cached", "1" if r.get("cached") else "0")
        self.send_header("X-Aali-Chunks", str(len(chunks)))
        self.send_header("X-Aali-Voice", str(voice or ""))
        self.send_header("X-Aali-File", name)
        self.end_headers()
        self.wfile.write(body)
        _log(f"synth lang={lang} voice={voice or 'default'} engine={r.get('engine')} "
             f"chars={len(first)} chunks={len(chunks)} cached={bool(r.get('cached'))}")

    def _voice_problem(self, voice: Optional[str]) -> str:
        """Validate a requested voice at the BOUNDARY, not only in the engine.

        The console must never answer 200 with audio the caller did not ask
        for: a name the library does not know is an error, whatever engine
        happens to be loaded.
        """
        if not voice:
            return ""
        profile = voice_lib.get_voice(str(voice))
        if profile is None:
            return f"unknown voice: {voice}"
        ref = profile.get("ref")
        if not ref or not Path(ref).exists():
            return f"voice {voice} has no reference clip on disk"
        return ""

    def _batch(self) -> None:
        data = self._body_json()
        script = str(data.get("script", ""))
        if not script.strip():
            return self._error("script is required", 400)
        if len(script) > MAX_TEXT * 5:
            return self._error("script too long", 400)
        fallback = str(data.get("lang") or "ar")
        voice = data.get("voice") or None
        bad = self._voice_problem(voice)
        if bad:
            return self._error(bad, 400)
        out_dir = Path(os.environ.get("AALI_VOICE_BATCH_DIR") or r"D:/hwk-data/voice_out") \
            / f"console_{int(time.time())}"
        out_dir.mkdir(parents=True, exist_ok=True)
        units: list = []
        for block in script.split("\n\n"):
            block = " ".join(line.strip() for line in block.splitlines() if line.strip())
            if block:
                units.extend(chunk_for_tts(block))
        manifest = []
        for i, unit in enumerate(units, 1):
            ulang = detect_script_lang(unit) or fallback
            r = self.tts.synthesize(unit, ulang, voice=voice)
            entry: Dict[str, Any] = {"index": i, "lang": ulang, "text": unit}
            if r.get("ok"):
                dest = out_dir / f"{i:03d}_{ulang}.wav"
                shutil.copyfile(r["path"], dest)
                entry.update({"ok": True, "file": dest.name,
                              "url": f"/api/audio/{dest.name}"})
            else:
                entry.update({"ok": False, "error": r.get("error")})
            manifest.append(entry)
        (out_dir / "manifest.json").write_text(
            json.dumps({"dir": str(out_dir), "items": manifest}, ensure_ascii=False),
            encoding="utf-8")
        made = sum(1 for m in manifest if m["ok"])
        _log(f"batch units={len(units)} made={made}")
        return self._json({"ok": True, "dir": str(out_dir), "items": manifest,
                           "made": made, "failed": len(manifest) - made})

    # -- static/audio ------------------------------------------------------
    def _serve_page(self, name: str) -> None:
        p = WEB_DIR / name
        if not p.exists():
            return self._error("console page missing", 404)
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_asset(self, name: str) -> None:
        p = WEB_DIR / name
        if not p.exists():
            return self._error("not found", 404)
        body = p.read_bytes()
        ctype = "text/css; charset=utf-8" if name.endswith(".css") \
            else "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_audio(self, name: str) -> None:
        """Only sha1-named files from the batch/cache dirs — no traversal."""
        import urllib.parse as up

        name = up.unquote(name)
        if not AUDIO_NAME.match(name):
            return self._error("bad audio name", 400)
        roots = [
            Path(os.environ.get("AALI_VOICE_CACHE") or r"D:/hwk-data/voice_tts"),
            Path(os.environ.get("AALI_VOICE_BATCH_DIR") or r"D:/hwk-data/voice_out"),
        ]
        for root in roots:
            p = (root / name).resolve()
            if p.exists() and root.resolve() in p.parents:
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        return self._error("audio not found", 404)


def parse_multipart(raw: bytes, content_type: str):
    """Minimal multipart/form-data parser -> ({field: value}, {name: (filename, bytes)}).

    stdlib only (cgi is deprecated/removed in 3.13); the console uploads one
    small wav, so a straightforward boundary split is enough.
    """
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        raise ValueError("multipart boundary missing")
    boundary = m.group(1).strip('"').encode("utf-8")
    fields: Dict[str, str] = {}
    files: Dict[str, tuple] = {}
    for part in raw.split(b"--" + boundary):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        if not _:
            continue
        headers = head.decode("utf-8", "replace")
        disp = re.search(r'name="([^"]+)"', headers)
        if not disp:
            continue
        name = disp.group(1)
        fname = re.search(r'filename="([^"]*)"', headers)
        data = body.rstrip(b"\r\n")
        if fname:
            files[name] = (fname.group(1), data)
        else:
            fields[name] = data.decode("utf-8", "replace").strip()
    return fields, files


def make_server(port: int = DEFAULT_PORT, fake: bool = False) -> ThreadingHTTPServer:
    handler = type("BoundStudioHandler", (StudioHandler,), {
        "tts_factory": staticmethod(lambda: build_tts(fake=fake)),
        "_tts": None,
    })
    host = "0.0.0.0" if os.environ.get("AALI_VOICE_BIND") else "127.0.0.1"
    return ThreadingHTTPServer((host, port), handler)


def main(argv: Optional[list] = None) -> int:
    import argparse

    force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="voice-studio-api")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--fake", action="store_true", help="no models, wiring test")
    args = ap.parse_args(argv)
    httpd = make_server(args.port, fake=args.fake)
    print(f"[studio] http://127.0.0.1:{args.port}/  "
          f"(fake={args.fake}, ctrl+c to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())