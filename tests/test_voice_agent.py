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
