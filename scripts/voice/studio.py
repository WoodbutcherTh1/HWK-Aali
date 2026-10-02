# -*- coding: utf-8 -*-
"""Aali Voice Studio — command line front for the voice asset library.

Subcommands (all local, all CPU, all honest):

  voices                 list every profile + per-language availability
  voices check FILE      validate a recorded clip before registering it
  voices add  NAME --lang ar --file clip.wav
  voices rm   NAME
  voices default ar NAME
  audition --voice NAME [--lang ar] [--text "..."]
  batch SCRIPT.txt [--voice NAME] [--out DIR]

`batch` is the production path: a plain text script (blank line = new
utterance) becomes numbered wavs plus a manifest, resuming — a re-run only
synthesizes what is missing.

Logs are content-free (names, counts, timings — never the script text).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# file_agent holds the Piper stack (the engine that carries Hebrew) — the
# voice venv does not have it installed as a package, but its subprocess
# python is resolved by absolute path, so importing it here is enough.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_REPO_ROOT, "file-agent")):
    sys.path.insert(0, os.path.join(_REPO_ROOT, "file-agent"))

from voice import force_utf8_stdio  # noqa: E402
from voice.core import voices as voice_lib  # noqa: E402
from voice.core.language_detect import chunk_for_tts, detect_script_lang  # noqa: E402

SAMPLES = {
    "ar": "مرحباً، هذه معاينة للصوت. إذا كنت تسمعني بوضوح، فالصوت جاهز.",
    "he": "שלום, זו הדגמה לקול. אם אתה שומע אותי בבירור, הקול מוכן.",
    "en": "Hello, this is a voice sample. If you can hear me clearly, the voice is ready.",
}
BATCH_OUT = Path(os.environ.get("AALI_VOICE_BATCH_DIR") or r"D:/hwk-data/voice_out")
LOG_FILE = voice_lib.LOG_FILE


def log(msg: str) -> None:
    print(msg, flush=True)


def content_log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# voices
# --------------------------------------------------------------------------

def cmd_voices(args: argparse.Namespace) -> int:
    action = args.action or "list"

    if action == "list":
        rep = voice_lib.library_report()
        if not rep["voices"]:
            log("لا توجد أصوات مسجّلة بعد. سجّل مقطعاً ثم: voices add NAME --lang ar --file clip.wav")
        for v in rep["voices"]:
            mark = " (افتراضي)" if v["is_default"] else ""
            state = "" if v["has_ref"] else "  ⚠ الملف مفقود"
            log(f"- {v['name']}  [{v['lang']}]{mark}{state}  {v['ref']}")
        log("")
        for lang, info in rep["languages"].items():
            if info["voice"]:
                status = f"صوت: {info['voice']} ({info['engine']})"
            elif info["engine"] == "piper":
                status = "صوت Piper مثبّت (بلا استنساخ)"
            elif info["recorded"]:
                status = "مكتشف لكن بلا مقطع"
            elif not rep.get("piper_stack") and lang == "he":
                # honest: XTTS cannot speak he; the Piper stack carries it
                status = "لا صوت — XTTS لا يدعم العبرية ومجموعة Piper غير متاحة هنا"
            else:
                status = "لا يوجد مقطع بعد"
            log(f"{lang}: {status}")
        if rep["piper"]:
            log("")
            log("أصوات Piper المثبّتة: " + ", ".join(
                f"{p['id']} [{p['language']}]" for p in rep["piper"]))
        elif not rep.get("piper_stack"):
            log("")
            log("⚠ مجموعة Piper غير متاحة في هذه البيئة (file_agent غير قابل للاستيراد)")
        return 0

    if action == "check":
        # the path is accepted positionally (`voices check FILE`) or via --file
        target = args.file or args.name
        if not target:
            log("مسار الملف مطلوب: voices check FILE")
            return 2
        verdict = voice_lib.validate_reference_wav(target)
        if verdict["ok"]:
            log(f"المقطع صالح: {verdict['seconds']}s · {verdict['sr']}Hz · "
                f"rms={verdict['rms']} · مقطع: {target}")
            return 0
        log(f"المقطع غير صالح: {verdict['error']}")
        return 1

    if action == "add":
        src = args.file or (args.name if args.name and args.name != "" else "")
        if not src:
            log("مسار المقطع مطلوب: --file clip.wav")
            return 2
        if not args.name or args.name == src:
            log("اسم الصوت مطلوب: voices add NAME --lang ar --file clip.wav")
            return 2
        try:
            r = voice_lib.add_voice(args.name, args.lang, src, note=args.note or "")
        except ValueError as exc:
            log(str(exc))
            return 2
        if not r["ok"]:
            log(f"رُفض الصوت: {r['error']}")
            return 1
        log(f"أُضيف الصوت {r['name']} ({r['lang']}) → {r['ref']}")
        return 0

    if action == "rm":
        r = voice_lib.delete_voice(args.name, keep_file=args.keep_file)
        log("حُذف الصوت." if r["ok"] else f"خطأ: {r['error']}")
        return 0 if r["ok"] else 1

    if action == "default":
        r = voice_lib.set_default(args.lang, args.name)
        log(f"الافتراضي لـ {args.lang}: {args.name}" if r["ok"] else f"خطأ: {r['error']}")
        return 0 if r["ok"] else 1

    log(f"أمر غير معروف: {action}")
    return 2


# --------------------------------------------------------------------------
# audition / batch
# --------------------------------------------------------------------------

def _build_tts():
    from voice.core.tts import VoiceTTS

    return VoiceTTS()


def _resolve_voice(name: str | None, lang: str) -> Dict[str, Any]:
    """Named voice, else the library default for the language, else {}."""
    if name:
        return voice_lib.get_voice(name) or {}
    d = voice_lib.default_voice(lang)
    return d or {}


def cmd_audition(args: argparse.Namespace) -> int:
    lang = args.lang
    text = args.text or SAMPLES.get(lang, SAMPLES["ar"])
    prof = _resolve_voice(args.voice, lang)
    tts = _build_tts()
    t0 = time.monotonic()
    r = tts.synthesize(text, lang, voice=prof.get("name") if prof else None)
    dt = time.monotonic() - t0
    if not r.get("ok"):
        log(f"فشل التوليد: {r.get('error')}")
        return 1
    content_log(f"audition lang={lang} voice={prof.get('name') or 'default'} "
                f"engine={r.get('engine')} {dt:.1f}s")
    log(f"الصوت: {prof.get('name') or '(افتراضي)'}")
    log(f"المحرك: {r.get('engine')} · {dt:.1f}s · {'من الذاكرة المؤقتة' if r.get('cached') else 'مولّد الآن'}")
    log(f"الملف: {r.get('path')}")
    if args.save:
        shutil.copyfile(r["path"], args.save)
        log(f"حُفظ → {args.save}")
    return 0


def _script_units(text: str) -> List[str]:
    """Blank line separates utterances; each utterance is then chunked to the
    engine's per-language cap so nothing is truncated."""
    units: List[str] = []
    for block in text.split("\n\n"):
        block = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if not block:
            continue
        units.extend(chunk_for_tts(block))
    return units


def cmd_batch(args: argparse.Namespace) -> int:
    script = Path(args.script)
    if not script.exists():
        log(f"ملف السكربت غير موجود: {script}")
        return 2
    out_dir = Path(args.out or BATCH_OUT) / script.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    units = _script_units(script.read_text(encoding="utf-8", errors="replace"))
    if not units:
        log("السكربت فارغ")
        return 2

    tts = _build_tts()
    made = reused = failed = 0
    manifest: List[Dict[str, Any]] = []
    for i, unit in enumerate(units, 1):
        lang = detect_script_lang(unit) or args.lang
        dest = out_dir / f"{i:03d}_{lang}.wav"
        prof = _resolve_voice(args.voice, lang)
        entry: Dict[str, Any] = {"index": i, "lang": lang, "voice": prof.get("name", "")}
        if dest.exists() and dest.stat().st_size > 1000 and not args.force:
            entry.update({"file": dest.name, "ok": True, "cached": True})
            reused += 1
            manifest.append(entry)
            log(f"[{i}/{len(units)}] موجود مسبقاً → {dest.name}")
            continue
        t0 = time.monotonic()
        r = tts.synthesize(unit, lang, voice=prof.get("name") if prof else None)
        dt = time.monotonic() - t0
        if r.get("ok"):
            shutil.copyfile(r["path"], dest)
            made += 1
            entry.update({"file": dest.name, "ok": True, "cached": bool(r.get("cached")),
                          "engine": r.get("engine"), "seconds": round(dt, 1)})
            log(f"[{i}/{len(units)}] ✓ {lang} · {r.get('engine')} · {dt:.1f}s · {dest.name}")
        else:
            failed += 1
            entry.update({"file": "", "ok": False, "error": r.get("error")})
            log(f"[{i}/{len(units)}] ✗ {lang} · {r.get('error')}")
        manifest.append(entry)

    man_path = out_dir / "manifest.jsonl"
    with open(man_path, "w", encoding="utf-8") as fh:
        for e in manifest:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    content_log(f"batch script={script.stem} units={len(units)} made={made} "
                f"reused={reused} failed={failed}")
    log(f"\nجاهز: {made} جديد · {reused} موجود · {failed} فشل → {out_dir}")
    return 0 if failed == 0 else 1


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="voice-studio")
    sub = ap.add_subparsers(dest="cmd")

    v = sub.add_parser("voices", help="manage the voice library")
    v.add_argument("action", nargs="?", default="list",
                   choices=["list", "check", "add", "rm", "default"])
    v.add_argument("name", nargs="?", default="")
    v.add_argument("--lang", default="ar", choices=["ar", "he", "en"])
    v.add_argument("--file", default="")
    v.add_argument("--note", default="")
    v.add_argument("--keep-file", action="store_true")

    a = sub.add_parser("audition", help="speak a sample line with a voice")
    a.add_argument("--voice", default=None)
    a.add_argument("--lang", default="ar", choices=["ar", "he", "en"])
    a.add_argument("--text", default="")
    a.add_argument("--save", default="")

    b = sub.add_parser("batch", help="synthesize a whole script to numbered wavs")
    b.add_argument("script")
    b.add_argument("--voice", default=None)
    b.add_argument("--lang", default="ar", choices=["ar", "he", "en"],
                   help="fallback when a line has no detectable script")
    b.add_argument("--out", default="")
    b.add_argument("--force", action="store_true")

    args = ap.parse_args()
    try:
        if args.cmd == "voices":
            return cmd_voices(args)
        if args.cmd == "audition":
            return cmd_audition(args)
        if args.cmd == "batch":
            return cmd_batch(args)
    except Exception as exc:  # honest failure, never a silent exit 0
        log(f"خطأ: {type(exc).__name__}: {exc}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())