# -*- coding: utf-8 -*-
"""Tests for the Aali voice agent (scripts/voice/, Phase 1).

Everything heavy is faked: no VAD/STT/TTS models, no GPU, no real Aali API
(except one hermetic localhost http server). The WS end-to-end test runs
only where `websockets` is installed (voice venv) and self-skips elsewhere.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import types
import wave
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from voice.core.barge_in import BargeInGate
from voice.core.language_detect import (
    LanguageTracker,
    chunk_for_tts,
    detect_script_lang,
)
from voice.core.pipeline import VoicePipeline, ask_aali
from voice.core.vad import SpeechSegmenter


# --------------------------------------------------------------------------
# language detection
# --------------------------------------------------------------------------

def test_script_detect_basic():
    assert detect_script_lang("مرحبا كيف حالك") == "ar"
    assert detect_script_lang("שלום עליכם") == "he"
    assert detect_script_lang("hello there") == "en"
    assert detect_script_lang("12345 ...") is None
    assert detect_script_lang("") is None


def test_script_detect_dominant_script_wins():
    mixed = "hello مرحبا مراحب"
    assert detect_script_lang(mixed) == "ar"
    mixed_he = "code שלום ומה"
    assert detect_script_lang(mixed_he) == "he"


def test_chunk_respects_max_chars():
    text = "جملة أولى قصيرة. جملة ثانية أطول قليلاً هنا. " * 6
    chunks = chunk_for_tts(text, max_chars=60)
    assert chunks
    assert all(len(c) <= 60 for c in chunks)
    assert "".join(chunks).replace(" ", "").startswith("جملة")


def test_chunk_code_switch_splits_by_language():
    text = "أهلا وسهلا! Hello and welcome. שלום לכם."
    chunks = chunk_for_tts(text, max_chars=240)
    assert chunks == ["أهلا وسهلا!", "Hello and welcome.", "שלום לכם."]
    assert [detect_script_lang(c) for c in chunks] == ["ar", "en", "he"]


def test_chunk_same_language_packs_together():
    text = "أهلا وسهلا. كيف الحال؟ بخير والحمد لله."
    chunks = chunk_for_tts(text, max_chars=240)
    assert chunks == [text]


def test_chunk_respects_engine_char_cap_per_language():
    """XTTS truncates past ~166 chars for ar/he — chunks must stay under the
    engine cap even when the caller's max_chars is generous (live warning)."""
    from voice.core.language_detect import ENGINE_CHAR_CAPS

    ar_reply = " ".join(["جملة عربية طويلة"] * 12) + "."
    chunks = chunk_for_tts(ar_reply, max_chars=400)
    assert len(chunks) > 1
    assert all(len(c) <= ENGINE_CHAR_CAPS["ar"] for c in chunks)

    en_reply = " ".join(["this is a long english sentence"] * 14) + "."
    en_chunks = chunk_for_tts(en_reply, max_chars=400)
    assert all(len(c) <= ENGINE_CHAR_CAPS["en"] for c in en_chunks)


def test_chunk_lang_caps_override_is_honest():
    text = "one two three four five six seven eight nine ten."
    assert chunk_for_tts(text, max_chars=200, lang_caps={"en": 15}) != [text]


# --------------------------------------------------------------------------
# language tracker
# --------------------------------------------------------------------------

def test_tracker_majority_vote_switches():
    tr = LanguageTracker(window=3)
    assert tr.update("ar", 0.9) == "ar"
    tr.update("ar", 0.9)
    tr.update("ar", 0.9)
    assert tr.update("en", 0.9) == "ar"  # 3-1 still ar
    tr.update("en", 0.9)
    assert tr.update("en", 0.9) == "en"  # 3-3 → latest majority wins
    assert tr.current == "en"


def test_tracker_ignores_low_confidence():
    tr = LanguageTracker()
    assert tr.update("en", 0.3) == "ar"  # below gate; default stays ar


# --------------------------------------------------------------------------
# segmenter (fake prob_fn)
# --------------------------------------------------------------------------

def _pcm(ms: float, sr: int = 16000) -> np.ndarray:
    n = int(sr * ms / 1000)
    return (np.sin(np.linspace(0, 300, n)) * 9000).astype("<i2")


def _seg(threshold=0.5, **kw) -> SpeechSegmenter:
    return SpeechSegmenter(prob_fn=lambda frame: 0.9, threshold=threshold, **kw)


def test_segmenter_open_close_with_preroll():
    s = _seg(pre_roll_ms=200, silence_ms_to_close=300, min_speech_ms=100)
    # 1s of hot audio
    out = s.feed(_pcm(1000))
    assert out == [] and s.speaking
    out = s.feed(_pcm(500))  # hot again, then a quiet tail via flush
    assert out == []
    seg = s.flush()
    assert seg is not None
    assert seg["sample_rate"] == 16000
    assert seg["ms"] >= 1400  # preroll + speech kept (silence trimmed at close)
    assert not s.speaking


def test_segmenter_silence_closes():
    calls = {"n": 0}

    def prob(frame):
        calls["n"] += 1
        return 0.9 if calls["n"] <= 5 else 0.1

    s = SpeechSegmenter(prob_fn=prob, threshold=0.5,
                        pre_roll_ms=100, silence_ms_to_close=350, min_speech_ms=100)
    out: list = []
    for _ in range(22):
        out.extend(s.feed(_pcm(32)))
    assert len(out) == 1 and not s.speaking
    assert out[0]["ms"] >= 200


def test_segmenter_drops_too_short():
    s = _seg(min_speech_ms=1000)
    s.feed(_pcm(200))
    seg = s.flush()
    assert seg is None


# --------------------------------------------------------------------------
# barge-in
# --------------------------------------------------------------------------

def test_barge_in_sustained_and_cooldown():
    t = [100.0]
    gate = BargeInGate(threshold=0.6, consecutive_frames=3, cooldown_s=1.0,
                       clock=lambda: t[0])
    assert gate.feed(0.9) is False
    assert gate.feed(0.9) is False
    assert gate.feed(0.9) is True          # 3rd consecutive fires
    assert gate.feed(0.9) is False         # cooldown active
    t[0] += 2.0
    assert gate.feed(0.2) is False
    assert gate.feed(0.2) is False
    assert gate.feed(0.9) is False         # run reset by cool frames
    assert gate.feed(0.9) is False
    assert gate.feed(0.9) is True


# --------------------------------------------------------------------------
# STT with a fake whisper model
# --------------------------------------------------------------------------

class _FakeWhisper:
    def __init__(self, text, lang, prob):
        self.text, self.lang, self.prob = text, lang, prob

    def transcribe(self, audio, **kw):
        seg = types.SimpleNamespace(text=self.text)
        info = types.SimpleNamespace(language=self.lang, language_probability=self.prob)
        return iter([seg]), info


def test_stt_script_overrides_low_confidence_misdetect():
    from voice.core.stt import WhisperSTT

    stt = WhisperSTT(model_size="x")
    stt._model = _FakeWhisper("שלום זה מה אני רוצה", "en", 0.6)  # clearly Hebrew
    res = stt.transcribe(_pcm(400))
    assert res["lang"] == "he"
    assert "שלום" in res["text"]


def test_stt_strong_detection_survives_script_check():
    from voice.core.stt import WhisperSTT

    stt = WhisperSTT(model_size="x")
    stt._model = _FakeWhisper("hello there friend", "en", 0.97)
    res = stt.transcribe(_pcm(400))
    assert res["lang"] == "en"


def test_stt_empty_audio_honest():
    from voice.core.stt import WhisperSTT

    stt = WhisperSTT(model_size="x")
    res = stt.transcribe(np.zeros(0, dtype="<i2"))
    assert res["text"] == "" and res["ms"] == 0


# --------------------------------------------------------------------------
# TTS (synth_fn seam + cache)
# --------------------------------------------------------------------------

def test_tts_synthfn_ok_and_fail(monkeypatch, tmp_path):
    import voice.core.tts as ttsmod

    monkeypatch.setattr(ttsmod, "CACHE_DIR", tmp_path)
    calls = []

    def ok_fn(text, lang, ref):
        calls.append((text, lang))
        return {"ok": True, "path": "fake://a", "sr": 24000, "engine": "fake"}

    t = ttsmod.VoiceTTS(synth_fn=ok_fn)
    r = t.synthesize("مرحبا", "ar")
    assert r["ok"] and r["engine"] == "fake" and calls == [("مرحبا", "ar")]

    def bad_fn(text, lang, ref):
        raise RuntimeError("boom")

    t2 = ttsmod.VoiceTTS(synth_fn=bad_fn)
    r2 = t2.synthesize("مرحبا", "ar")
    assert not r2["ok"] and "synth_fn" in r2["error"]


def test_tts_empty_text_honest():
    from voice.core.tts import VoiceTTS

    r = VoiceTTS(synth_fn=lambda *a: {"ok": True}).synthesize("  ", "ar")
    assert not r["ok"] and "empty" in r["error"]


def test_tts_cache_hit(monkeypatch, tmp_path):
    import voice.core.tts as ttsmod

    monkeypatch.setattr(ttsmod, "CACHE_DIR", tmp_path)
    t = ttsmod.VoiceTTS(synth_fn=lambda *a: {"ok": False})  # would fail if called
    text = "مساء الخير"
    ref_key = t._ref_for("ar") or "none"  # digest must use the resolved ref
    digest = ttsmod.hashlib.sha1(
        f"xtts|ar|{ref_key}|".encode("utf-8") + text.encode("utf-8")
    ).hexdigest()
    wav = tmp_path / f"{digest}.wav"
    wav.write_bytes(b"x" * 2000)
    r = t.synthesize(text, "ar")
    assert r["ok"] and r["cached"] is True


# --------------------------------------------------------------------------
# pipeline (fakes everywhere)
# --------------------------------------------------------------------------

class _FakeSeg:
    def __init__(self, ms=500):
        self.ms = ms

    def feed(self, pcm):
        return [{"sample_rate": 16000, "pcm": pcm, "ms": self.ms}]


class _FakeSTT:
    def transcribe(self, pcm, sample_rate=16000):
        return {"text": "شو الأخبار", "lang": "ar", "confidence": 0.95, "ms": 3}


class _FakeTTS:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, lang="ar"):
        self.calls.append((text, lang))
        return {"ok": True, "path": "fake://x", "sr": 24000, "engine": "fake"}


def _pipe(reply="أهلاً بك! Hello there. שלום חבר.", tts=None):
    tts = tts or _FakeTTS()
    asks = []

    def ask(message, sid, base_url="", api_key="", timeout_s=10.0):
        asks.append({"message": message, "sid": sid, "api_key": api_key})
        return {"reply": reply, "sid": sid}

    p = VoicePipeline(segmenter=_FakeSeg(), stt=_FakeSTT(), tts=tts, ask_fn=ask)
    p._asks = asks
    return p


def test_pipeline_turn_end_to_end_code_switch():
    p = _pipe()
    chunks_seen = []
    res = p.run_turn({"sample_rate": 16000, "pcm": _pcm(300), "ms": 300},
                     on_chunk=chunks_seen.append)
    assert res["ok"] and res["user_text"] == "شو الأخبار"
    assert res["user_lang"] == "ar"
    langs = [c["lang"] for c in res["chunks"]]
    assert "ar" in langs and "en" in langs and "he" in langs  # per-sentence tongue
    assert len(chunks_seen) == len(res["chunks"])
    assert p._asks[0]["sid"].startswith("voice-")
    assert p._asks[0]["api_key"] == ""


def test_pipeline_empty_reply_is_honest():
    p = _pipe(reply="")
    res = p.run_turn({"sample_rate": 16000, "pcm": _pcm(300), "ms": 300})
    assert not res["ok"] and "empty reply" in res["error"]


def test_pipeline_ask_failure_is_an_honest_dict_not_a_traceback():
    def boom(**_kw):
        raise RuntimeError("aali refused the ask (HTTP 404)")

    p = _pipe()
    p.ask_fn = boom
    res = p.run_turn({"sample_rate": 16000, "pcm": _pcm(300), "ms": 300})
    assert not res["ok"] and "404" in res["error"]
    assert res["user_text"] == "شو الأخبار"  # the turn still reports what it heard
    assert p.session_state(res["sid"])["tts_speaking"] is False


def test_ask_aali_key_mode_404_explains_the_key():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError) as ei:
            ask_aali("سلام", "voice-x", base_url=f"http://127.0.0.1:{srv.server_address[1]}")
        assert "KEY MODE" in str(ei.value) and "AALI_VOICE_API_KEY" in str(ei.value)
    finally:
        srv.shutdown()


def test_pipeline_feed_audio_suppressed_while_speaking():
    p = _pipe()
    st = p.session_state("s1")
    st["tts_speaking"] = True
    assert p.feed_audio(_pcm(300), "s1") == []
    st["tts_speaking"] = False
    assert len(p.feed_audio(_pcm(300), "s1")) == 1


def test_ask_aali_contract():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["body"] = body
            seen["key"] = self.headers.get("X-API-Key")
            out = json.dumps({"ok": True, "reply": f"قلت: {body['message']}",
                              "sid": body["sid"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = ask_aali("سلام", "voice-x", base_url=f"http://127.0.0.1:{srv.server_address[1]}",
                     api_key="k123", timeout_s=5)
        assert r["reply"] == "قلت: سلام" and r["sid"] == "voice-x"
        assert seen["key"] == "k123" and seen["body"]["sid"] == "voice-x"
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------
# UTF-8 stdio (cp1252 consoles killed the first live chain mid-log)
# --------------------------------------------------------------------------

def test_force_utf8_stdio_reconfigures_a_cp1252_stream():
    import io

    from voice import force_utf8_stdio

    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf, buf
    try:
        force_utf8_stdio()
        print("مرحبا שלום hello")
        buf.flush()
        assert buf.buffer.getvalue().decode("utf-8").strip() == "مرحبا שלום hello"
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def test_voice_entrypoints_force_utf8_before_printing():
    """Tripwire: cli.py + chain_e2e.py must force UTF-8 (regression: the live
    chain died on UnicodeEncodeError right after a successful turn)."""
    from pathlib import Path

    voice_dir = Path(__file__).resolve().parents[1] / "scripts" / "voice"
    for name in ("cli.py", "chain_e2e.py"):
        src = (voice_dir / name).read_text(encoding="utf-8")
        assert "force_utf8_stdio" in src, f"{name} never forces utf-8 stdio"


def test_xtts_language_set_excludes_hebrew():
    """Hebrew is NOT an XTTS-v2 language (verified against the local
    config.json) — routing it to XTTS only produces a mid-run failure."""
    from voice.core.tts import XTTS_LANGS

    assert "ar" in XTTS_LANGS and "en" in XTTS_LANGS
    assert "he" not in XTTS_LANGS


def test_hebrew_never_reaches_xtts_and_never_borrows_the_arabic_voice(monkeypatch):
    from voice.core.tts import VoiceTTS

    calls = []

    class FakeXtts:
        def inference(self, **kw):
            calls.append(kw)
            return {"wav": [0.0] * 2400}

    t = VoiceTTS()
    monkeypatch.setattr(t, "xtts_ready", lambda: True)
    monkeypatch.setattr(t, "_ref_for", lambda lang: "ref.wav")
    monkeypatch.setattr(t, "ensure_xtts", lambda: None)
    monkeypatch.setattr(t, "_xtts", FakeXtts())
    monkeypatch.setattr(t, "_piper_voice_for", lambda lang: None)

    r = t.synthesize("שלום", "he")
    assert calls == []                       # XTTS never asked
    assert not r["ok"] and "he" in r["error"] and "Piper" in r["error"]


def test_piper_fallback_picks_a_matching_language_voice(monkeypatch):
    """The fallback must never speak English/Hebrew in the Arabic voice."""
    import sys
    import types as _types

    from voice.core.tts import VoiceTTS

    seen = {}
    fake = _types.ModuleType("file_agent.tts")
    fake.list_voices = lambda: [
        {"id": "ar_JO-kareem-medium", "language": "ar"},
        {"id": "en_US-lessac-medium", "language": "en"},
    ]

    def synthesize(text, voice_id=None, fmt="wav"):
        seen["voice_id"] = voice_id
        return {"ok": False, "error": "no piper binary in tests"}   # stop here

    fake.synthesize = synthesize
    pkg = _types.ModuleType("file_agent")
    pkg.tts = fake
    monkeypatch.setitem(sys.modules, "file_agent", pkg)
    monkeypatch.setitem(sys.modules, "file_agent.tts", fake)

    t = VoiceTTS()
    assert t._piper_voice_for("en") == "en_US-lessac-medium"
    assert t._piper_voice_for("ar") == "ar_JO-kareem-medium"
    assert t._piper_voice_for("he") is None      # nothing installed -> honest
    t._piper("hello", "en")
    assert seen["voice_id"] == "en_US-lessac-medium"


# --------------------------------------------------------------------------
# voice library (Phase 2 studio)
# --------------------------------------------------------------------------

def _ref_wav(path, seconds=6.0, sr=24000, amp=0.3):
    n = int(sr * seconds)
    t = np.linspace(0, seconds, n, endpoint=False)
    tone = (np.sin(2 * np.pi * 180 * t) * amp * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(tone.tobytes())
    return path


@pytest.fixture()
def lib(tmp_path):
    """An isolated library: its own store dir and its own refs dir."""
    from voice.core import voices as voice_lib

    store = tmp_path / "voices.json"
    refs = tmp_path / "refs"
    return voice_lib, store, refs


def test_validate_accepts_a_clean_mono_clip(lib, tmp_path):
    voice_lib, _, _ = lib
    clip = _ref_wav(tmp_path / "ok.wav", seconds=8.0, amp=0.3)
    v = voice_lib.validate_reference_wav(clip)
    assert v["ok"] and v["sr"] == 24000 and v["channels"] == 1
    assert 7.9 <= v["seconds"] <= 8.1 and v["reasons"] == []


def test_validate_names_every_real_defect(lib, tmp_path):
    voice_lib, _, _ = lib
    short = _ref_wav(tmp_path / "short.wav", seconds=1.0, amp=0.3)
    v = voice_lib.validate_reference_wav(short)
    assert not v["ok"] and any("قصير" in r for r in v["reasons"])

    quiet = _ref_wav(tmp_path / "quiet.wav", seconds=8.0, amp=0.0005)
    v2 = voice_lib.validate_reference_wav(quiet)
    assert not v2["ok"] and any("منخفض" in r for r in v2["reasons"])

    stereo = tmp_path / "stereo.wav"
    with wave.open(str(stereo), "wb") as wf:
        wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(24000)
        wf.writeframes(np.zeros(24000 * 8 * 2, dtype="<i2").tobytes())
    v3 = voice_lib.validate_reference_wav(stereo)
    assert not v3["ok"] and any("mono" in r for r in v3["reasons"])

    v4 = voice_lib.validate_reference_wav(tmp_path / "nope.wav")
    assert not v4["ok"] and v4["error"] == "missing file"


def test_add_voice_refuses_a_bad_clip_and_registers_a_good_one(lib, tmp_path):
    voice_lib, store, refs = lib
    bad = _ref_wav(tmp_path / "bad.wav", seconds=1.0)
    r = voice_lib.add_voice("shorty", "ar", bad, store_path=store, refs_dir=refs)
    assert not r["ok"] and "قصير" in r["error"]
    assert voice_lib.list_voices(voice_lib.load_store(store)) == []   # nothing registered

    good = _ref_wav(tmp_path / "good.wav", seconds=9.0)
    ok = voice_lib.add_voice("صوت_عربي", "ar", good, note="صوت المالك",
                             store_path=store, refs_dir=refs)
    assert ok["ok"] and Path(ok["ref"]).exists()
    listed = voice_lib.list_voices(voice_lib.load_store(store))
    assert len(listed) == 1 and listed[0]["name"] == "صوت_عربي"
    assert listed[0]["is_default"] is True and listed[0]["has_ref"] is True


def test_default_voice_per_language_and_fallthrough(lib, tmp_path, monkeypatch):
    voice_lib, store, refs = lib
    # hermetic: no host Piper voices leaking into the verdict
    monkeypatch.setattr(voice_lib, "_piper_list", lambda: (True, []))
    ar = _ref_wav(tmp_path / "ar.wav", seconds=8.0)
    voice_lib.add_voice("ar1", "ar", ar, store_path=store, refs_dir=refs)
    assert voice_lib.default_voice("ar", voice_lib.load_store(store))["name"] == "ar1"

    # Hebrew exists as a PROFILE but its clip is gone -> not available, and
    # the studio must not pretend a voice exists.
    he = _ref_wav(tmp_path / "he.wav", seconds=8.0)
    voice_lib.add_voice("he1", "he", he, store_path=store, refs_dir=refs)
    Path(voice_lib.get_voice("he1", voice_lib.load_store(store))["ref"]).unlink()
    assert voice_lib.default_voice("he", voice_lib.load_store(store)) is None
    rep = voice_lib.library_report(store)
    assert rep["languages"]["he"]["recorded"] is True
    assert rep["languages"]["he"]["available"] is False
    assert rep["languages"]["en"]["recorded"] is False


def test_delete_voice_repoints_or_clears_the_default(lib, tmp_path):
    voice_lib, store, refs = lib
    for name in ("ar1", "ar2"):
        voice_lib.add_voice(name, "ar", _ref_wav(tmp_path / f"{name}.wav", 8.0),
                            store_path=store, refs_dir=refs)
    voice_lib.set_default("ar", "ar2", store_path=store)
    r = voice_lib.delete_voice("ar2", store_path=store)
    assert r["ok"]
    st = voice_lib.load_store(store)
    assert st["defaults"]["ar"] == "ar1"          # never dangles
    voice_lib.delete_voice("ar1", store_path=store)
    assert "ar" not in voice_lib.load_store(store)["defaults"]


def test_set_default_rejects_a_language_mismatch(lib, tmp_path):
    voice_lib, store, refs = lib
    voice_lib.add_voice("en1", "en", _ref_wav(tmp_path / "en.wav", 8.0),
                        store_path=store, refs_dir=refs)
    r = voice_lib.set_default("ar", "en1", store_path=store)
    assert not r["ok"] and "en" in r["error"]


def test_named_voice_synthesis_is_honest_and_isolated(lib, tmp_path, monkeypatch):
    """A named voice that does not exist must fail loudly, NOT silently speak
    with the per-language reference (the Arabic-accent trap)."""
    import voice.core.tts as ttsmod
    from voice.core.tts import VoiceTTS

    voice_lib, store, refs = lib
    monkeypatch.setattr(voice_lib, "STORE_PATH", store)
    seen = []

    def synth_fn(text, lang, ref):
        seen.append((text, lang, ref))
        return {"ok": True, "path": "fake://x", "sr": 24000, "engine": "fake"}

    t = VoiceTTS(synth_fn=synth_fn)
    missing = t.synthesize("مرحبا", "ar", voice="ghost")
    assert not missing["ok"] and "ghost" in missing["error"]
    assert seen == []                       # nothing was synthesized at all

    voice_lib.add_voice("he_voice", "he", _ref_wav(tmp_path / "he.wav", 8.0),
                        store_path=store, refs_dir=refs)
    ok = t.synthesize("שלום", "he", voice="he_voice")
    assert ok["ok"] and ok["voice"] == "he_voice"
    assert seen and seen[-1][2].endswith("he_voice.wav")   # the NAMED ref


def test_library_report_names_the_engine_per_language(lib, tmp_path, monkeypatch):
    """Hebrew has no XTTS voice: the board must say Piper, not 'ready'."""
    from voice.core import voices as voice_lib

    voice_lib, store, refs = lib
    monkeypatch.setattr(voice_lib, "_piper_list", lambda: (True, [
        {"id": "ar_JO-kareem-medium", "language": "ar_JO"},
        {"id": "he_IL-saspeech-medium", "language": "he_IL"},
    ]))
    voice_lib.add_voice("ar1", "ar", _ref_wav(tmp_path / "a.wav", 8.0),
                        store_path=store, refs_dir=refs)
    rep = voice_lib.library_report(store)
    assert rep["languages"]["ar"]["engine"] == "xtts"     # has a clone ref
    assert rep["languages"]["he"]["engine"] == "piper"    # no XTTS he -> piper
    assert rep["languages"]["he"]["available"] is True
    assert rep["languages"]["en"]["engine"] is None and rep["languages"]["en"]["available"] is False


def test_corrupt_store_reads_as_empty_not_a_crash(lib):
    voice_lib, store, _ = lib
    store.write_text("{not json", encoding="utf-8")
    assert voice_lib.load_store(store)["voices"] == {}


# --------------------------------------------------------------------------
# server helpers + WS e2e (websockets required)
# --------------------------------------------------------------------------

def test_wav_to_pcm16_roundtrip(tmp_path):
    pytest.importorskip("websockets")  # voice.core.server imports lazily but
    from voice.core.server import wav_to_pcm16  # the helpers live beside it

    p = tmp_path / "t.wav"
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        wf.writeframes(_pcm(100).tobytes())
    raw = wav_to_pcm16(str(p))
    assert np.frombuffer(raw, dtype="<i2").shape[0] == 1600


def test_fake_chunk_pcm_valid():
    pytest.importorskip("websockets")
    from voice.core.server import fake_chunk_pcm

    raw = fake_chunk_pcm("مرحبا")
    arr = np.frombuffer(raw, dtype="<i2")
    assert arr.shape[0] >= 800 and np.abs(arr.astype(np.int32)).max() > 100


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_server_e2e_fake_pipeline():
    websockets = pytest.importorskip("websockets")
    from websockets.sync.client import connect as ws_connect

    from voice.core.server import VoiceServer, _fake_pipeline

    ws_port, http_port = _free_port(), _free_port()
    srv = VoiceServer(_fake_pipeline(), ws_port=ws_port, http_port=http_port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", ws_port), timeout=0.3)
            s.close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail("voice WS server never came up")

    with ws_connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as ws:
        hello = json.loads(ws.recv(timeout=5))
        # server always supplies its own per-connection sid (voice-*)
        assert hello["type"] == "hello" and hello["sid"].startswith("voice-")
        ws.send(_pcm(1000).tobytes())  # 1s speech -> fake segment
        events, audio_frames, saw_done = [], 0, False
        while not saw_done and len(events) < 30:
            msg = ws.recv(timeout=10)
            if isinstance(msg, (bytes, bytearray)):
                audio_frames += 1
                continue
            ev = json.loads(msg)
            events.append(ev["type"])
            if ev["type"] == "turn_done":
                saw_done = True
        assert saw_done
        assert "transcript" in events and "reply" in events and "chunk_start" in events
        assert audio_frames >= 1
        ws.send(json.dumps({"type": "status"}))
        st = json.loads(ws.recv(timeout=5))
        assert st["type"] == "status"
