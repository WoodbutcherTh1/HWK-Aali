# -*- coding: utf-8 -*-
"""Voice agent server: WebSocket :5080 (PCM in, events + audio out) and a
static file server for web/voice/ on :5081.

Protocol (client -> server):
  binary frames  = raw 16k mono int16 PCM from the mic (continuous)
  text frames    = JSON control: {"type":"status"} | {"type":"ping"}

Protocol (server -> client):
  binary frames  = raw 24k mono int16 PCM of one TTS chunk (meta arrives
                   first as {"type":"chunk_start", sr, lang, engine})
  text frames    = JSON events: hello | transcript | reply | chunk_start |
                   turn_done | barge_in | status | error | pong

Barge-in (Phase 1 honest version): a completed speech segment while a reply
is being sent cancels the REMAINING chunks and queues a new turn. Without
echo cancellation the interrupting segment may contain Aali's own voice —
the gate requires sustained speech + cooldown; a real AEC is a later phase.

AALI_VOICE_FAKE=1 builds a pipeline with deterministic seams (no models, no
network) so tests exercise the real server end-to-end on CPU.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from voice.core.pipeline import VoicePipeline
from voice.core.vad import SpeechSegmenter

# websockets imports lazily (tests run the fake pipeline in venvs without it)

WEB_DIR = Path(__file__).resolve().parents[3] / "web" / "voice"
REPO_ROOT = Path(__file__).resolve().parents[3] / "file-agent"


# --------------------------------------------------------------------------
# pipeline construction (real or fake)
# --------------------------------------------------------------------------

def build_pipeline(host_cfg: Dict[str, Any], fake: bool = False) -> VoicePipeline:
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/

    if fake:
        return _fake_pipeline()
    from voice.core.stt import WhisperSTT
    from voice.core.tts import VoiceTTS
    from voice.core.vad import default_prob_fn

    stt_cfg = host_cfg.get("stt", {})
    tts_cfg = host_cfg.get("tts", {})
    segmenter = SpeechSegmenter(
        prob_fn=default_prob_fn(),
        threshold=host_cfg.get("vad", {}).get("threshold", 0.5),
        pre_roll_ms=host_cfg.get("vad", {}).get("pre_roll_ms", 380),
        silence_ms_to_close=host_cfg.get("vad", {}).get("silence_ms_to_close", 700),
        min_speech_ms=host_cfg.get("vad", {}).get("min_speech_ms", 200),
    )
    stt = WhisperSTT(
        model_size=stt_cfg.get("model", "large-v3-turbo"),
        device=stt_cfg.get("device", "cpu"),
        compute_type=stt_cfg.get("compute_type", "int8"),
        model_dir=stt_cfg.get("model_dir"),
    )
    tts = VoiceTTS(
        refs=tts_cfg.get("references") or None,
        fallback_ref_lang=tts_cfg.get("fallback_ref_lang", "ar"),
        xtts_dir=tts_cfg.get("xtts_snapshot_dir") or None,
    )
    return VoicePipeline(
        segmenter=segmenter,
        stt=stt,
        tts=tts,
        base_url=host_cfg.get("llm", {}).get("base_url", "http://127.0.0.1:5055"),
        api_key=os.environ.get("AALI_VOICE_API_KEY") or os.environ.get("AALI_API_KEY", ""),
    )


def _fake_pipeline() -> VoicePipeline:
    """Deterministic pipeline for tests: fixed transcript, echoed reply,
    tiny valid wav. No models, no network, no files outside the cache."""
    import numpy as np

    class FakeSegmenter:
        def __init__(self):
            self.speaking = False

        def feed(self, pcm16, _np=np):
            if pcm16 is None or pcm16.size < 1600:  # <100ms is noise
                return []
            return [{"sample_rate": 16000, "pcm": pcm16, "ms": 100.0 * pcm16.size / 1600}]

        def flush(self):
            return None

    class FakeSTT:
        def transcribe(self, pcm16, sample_rate=16000):
            return {"text": "مرحبا يا عالي", "lang": "ar", "confidence": 0.99, "ms": 5}

    class FakeTTS:
        def synthesize(self, text, lang="ar"):
            return {"ok": True, "path": "fake://pcm", "sr": 24000, "engine": "fake",
                    "cached": False, "text": text}

        def engine_report(self):
            return {"xtts": True, "piper": True, "fake": True}

    def fake_ask(message, sid, base_url="", api_key="", timeout_s=10.0):
        return {"reply": f"أهلاً! سمعت: {message}", "sid": sid}

    return VoicePipeline(
        segmenter=FakeSegmenter(), stt=FakeSTT(), tts=FakeTTS(), ask_fn=fake_ask,
        sid_prefix="voice-test-",
    )


# --------------------------------------------------------------------------
# wav -> pcm helper (tests pin this)
# --------------------------------------------------------------------------

def wav_to_pcm16(path: str) -> bytes:
    """Read a wav file as raw int16 mono PCM bytes (header stripped).

    The stdlib wave module first (fast path); soundfile fallback covers
    float32/other-format wavs (e.g. older XTTS outputs) converted honestly
    to int16.
    """
    try:
        with wave.open(path, "rb") as wf:
            if wf.getsampwidth() != 2:
                raise wave.Error("not 16-bit")
            return wf.readframes(wf.getnframes())
    except (wave.Error, OSError):
        import numpy as np
        import soundfile as sf

        data, _sr = sf.read(path, dtype="int16")
        return np.asarray(data, dtype="<i2").tobytes()


def fake_chunk_pcm(text: str) -> bytes:
    """Non-silent placeholder audio for the fake engine (valid int16 PCM)."""
    import numpy as np

    n = max(800, min(len(text) * 160, 48000))
    t = np.arange(n, dtype=np.float32) / 24000.0
    pcm = (np.sin(2 * np.pi * 220.0 * t) * 8000).astype("<i2")
    return pcm.tobytes()


# --------------------------------------------------------------------------
# servers
# --------------------------------------------------------------------------

def make_static_server(port: int, root: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, fmt, *args):  # content-free: paths only, no queries
            pass

    return ThreadingHTTPServer(("0.0.0.0" if os.environ.get("AALI_VOICE_BIND") else "127.0.0.1", port), Handler)


class VoiceServer:
    def __init__(self, pipeline: VoicePipeline, ws_port: int = 5080, http_port: int = 5081):
        self.pipeline = pipeline
        self.ws_port = ws_port
        self.http_port = http_port
        self._conns: Dict[Any, Dict[str, Any]] = {}

    # -- per-connection worker ------------------------------------------
    def _turn_worker(self, ws, state):
        """Serial turn executor.

        Barge-in semantics (Phase 1, honest): audio arriving WHILE TTS
        chunks are streaming cancels the REMAINING chunks; the completed
        interrupting segment is then served as the next turn.
        """
        while True:
            seg = state["queue"].get()
            if seg is None:
                return
            sid = state["sid"]

            def on_chunk(item, _ws=ws, _state=state):
                if not item.get("ok"):
                    _safe_send_text(_ws, {"type": "error", "message": f"tts: {item.get('error')}"})
                    return
                _safe_send_text(_ws, {
                    "type": "chunk_start", "text": item.get("text", ""),
                    "lang": item.get("lang"), "engine": item.get("engine"),
                    "sr": item.get("sr"),
                })
                if item.get("engine") == "fake" or str(item.get("path", "")).startswith("fake://"):
                    pcm = fake_chunk_pcm(item.get("text", "x"))
                else:
                    try:
                        pcm = wav_to_pcm16(item["path"])
                    except Exception as exc:
                        _safe_send_text(_ws, {"type": "error", "message": f"audio: {exc}"})
                        return
                if _state["cancel"].is_set():
                    return  # barge-in: drop the rest of this reply
                _safe_send_binary(_ws, pcm)

            try:
                state["speaking"] = True  # cancels fire while chunks stream
                _safe_send_text(ws, {"type": "transcript", "sid": sid})
                res = self.pipeline.run_turn(seg, sid=sid, on_chunk=on_chunk)
                if res.get("ok") or res.get("partial"):
                    _safe_send_text(ws, {"type": "reply", "text": res.get("reply", "")})
                _safe_send_text(ws, {
                    "type": "turn_done", "ok": bool(res.get("ok")),
                    "partial": bool(res.get("partial")),
                    "error": res.get("error"),
                })
            except Exception as exc:
                _safe_send_text(ws, {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                state["cancel"].clear()
                state["speaking"] = False

    # -- websocket handler -------------------------------------------------
    async def _ws_handler(self, ws):
        import websockets  # lazy (module top must stay import-safe without it)

        ws._voice_loop = asyncio.get_running_loop()  # worker threads send via this
        state = {
            "sid": f"voice-{os.getpid()}-{id(ws) % 100000}",
            "queue": queue.Queue(),
            "speaking": False,
            "cancel": threading.Event(),
        }
        self._conns[ws] = state
        state["sid"] = self.pipeline.session_state(state["sid"])["sid"]
        _safe_send_text(ws, {"type": "hello", "sid": state["sid"]})
        worker = threading.Thread(target=self._turn_worker, args=(ws, state), daemon=True)
        worker.start()
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    import numpy as np

                    pcm = np.frombuffer(raw, dtype="<i2")
                    if state["speaking"] and not state["cancel"].is_set():
                        # user audio during streamed playback -> cancel the
                        # remaining chunks; the segment below becomes the
                        # next turn (worker serves the queue serially)
                        state["cancel"].set()
                        _safe_send_text(ws, {"type": "barge_in"})
                    segs = self.pipeline.segmenter.feed(pcm)
                    for seg in segs:
                        state["queue"].put(seg)
                else:
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    mtype = msg.get("type")
                    if mtype == "ping":
                        _safe_send_text(ws, {"type": "pong"})
                    elif mtype == "status":
                        _safe_send_text(ws, {"type": "status", **self.pipeline.status(),
                                             "sid": state["sid"]})
        except websockets.ConnectionClosed:
            pass
        finally:
            state["queue"].put(None)
            self._conns.pop(ws, None)

    # -- lifecycle -----------------------------------------------------------
    async def _serve_ws(self):
        import websockets

        async with websockets.serve(self._ws_handler, "0.0.0.0" if os.environ.get("AALI_VOICE_BIND") else "127.0.0.1", self.ws_port):
            await asyncio.Future()  # run forever

    def serve_forever(self) -> None:
        httpd = make_static_server(self.http_port, WEB_DIR)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"[voice] UI: http://127.0.0.1:{self.http_port}/  WS: ws://127.0.0.1:{self.ws_port}", flush=True)
        asyncio.run(self._serve_ws())


def _safe_send_text(ws, obj: Dict[str, Any]) -> None:
    """Send a JSON event from any thread via the connection's event loop."""
    try:
        loop = ws._voice_loop  # type: ignore[attr-defined]
        asyncio.run_coroutine_threadsafe(ws.send(json.dumps(obj, ensure_ascii=False)), loop)
    except Exception:
        pass


def _safe_send_binary(ws, pcm: bytes) -> None:
    try:
        loop = ws._voice_loop  # type: ignore[attr-defined]
        asyncio.run_coroutine_threadsafe(ws.send(pcm), loop)
    except Exception:
        pass
