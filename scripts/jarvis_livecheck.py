"""Live end-to-end check, run on the Pi with the real Groq key.

Proves the three network-dependent things the bot depends on:
  1. Groq chat answers (Arabic and English)
  2. espeak-ng -> OGG/Opus produces a real voice note
  3. Whisper transcribes that speech back (a genuine speech-to-speech round trip)

Secrets are read from the config dir and never printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "jarvis"))

from jarvis import groq, tts  # noqa: E402
from jarvis.config import load_groq  # noqa: E402
from jarvis.log import log_event  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    results.append((name, PASS if ok else FAIL, note))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {note}" if note else ""))


def check_chat(cfg) -> None:
    for label, prompt in (
        ("chat-arabic", "من أنت؟ أجب بجملة واحدة."),
        ("chat-english", "Who are you? Answer in one sentence."),
    ):
        try:
            reply = groq.chat(cfg, prompt)
            ok = bool(reply) and len(reply) > 3
            record(label, ok, f"{len(reply)} chars")
            print(f"       -> {reply[:90]}")
        except groq.GroqError as exc:
            record(label, False, str(exc)[:80])


def check_tts() -> None:
    if not tts.available():
        record("tts-tools", False, "espeak-ng or ffmpeg missing")
        return
    record("tts-tools", True, "espeak-ng + ffmpeg present")
    for label, text in (("tts-arabic", "جارفيس مستيقظ"), ("tts-english", "Jarvis is online")):
        try:
            out = Path("/tmp/jarvis_live") / f"{label}.ogg"
            out.parent.mkdir(parents=True, exist_ok=True)
            path = tts.synthesize(text, out_path=out)
            data = path.read_bytes()
            ok = data[:4] == b"OggS" and len(data) > 800
            record(label, ok, f"{len(data)} bytes, magic={data[:4]!r}")
        except Exception as exc:
            record(label, False, f"{type(exc).__name__}: {exc}")


def check_speech_roundtrip(cfg) -> None:
    """Speak with espeak, transcribe with Whisper, compare."""
    phrase = "turn on the computer"
    try:
        wav = Path("/tmp/jarvis_live/roundtrip.wav")
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"")
    except OSError:
        pass
    try:
        # espeak directly to wav (no opus, whisper takes wav fine)
        from jarvis.tts import _run, build_env, espeak_path

        run = _run([espeak_path(), "-v", "en", "-w", str(wav), phrase], build_env(), 60)
        if run[0] != 0 or not wav.exists():
            record("stt-roundtrip", False, "espeak could not make the test wav")
            return
        text = groq.transcribe(cfg, wav.read_bytes(), filename="rt.wav")
        ok = bool(text.strip())
        heard = text.strip().lower()
        match = any(w in heard for w in ("computer", "turn", "on"))
        record("stt-roundtrip", ok, f'heard: "{text.strip()[:60]}"')
        record("stt-matched-phrase", match, f"expected words from '{phrase}'")
    except Exception as exc:
        record("stt-roundtrip", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print("=== Jarvis live check (real Groq, real audio tools) ===\n")
    try:
        cfg = load_groq()
    except Exception as exc:
        print(f"cannot load the Groq key: {exc}")
        return 1

    check_chat(cfg)
    check_tts()
    check_speech_roundtrip(cfg)

    failed = [r for r in results if r[1] == FAIL]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for name, status, note in failed:
        print(f"  FAILED: {name} -- {note}")
    log_event("livecheck", count=len(results), ok=len(failed) == 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())