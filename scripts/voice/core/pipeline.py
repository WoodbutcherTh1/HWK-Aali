# -*- coding: utf-8 -*-
"""Voice pipeline orchestrator: segment -> STT -> Aali -> TTS chunks.

Turn flow:
  1. the server streams 16k int16 PCM frames (WS binary) into a
     SpeechSegmenter;
  2. a completed segment goes to WhisperSTT -> {text, lang};
  3. text goes to Aali (:5055 /api/ask; key from AALI_VOICE_API_KEY or
     AALI_API_KEY; session id `voice-*` shows in the sidebar);
  4. the reply is chunked sentence-first; EACH chunk gets its own script
     language so a code-switched reply speaks each sentence in its own
     tongue (chunk_for_tts + detect_script_lang);
  5. chunks synthesize (XTTS clone / piper fallback) and stream back as
     WAV bytes with metadata events.

Every heavy dependency is injectable (stt/tts/ask_fn/prob_fn) so tests run
the full turn logic without models, the GPU, or the network.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional

from voice.core.barge_in import BargeInGate
from voice.core.language_detect import (
    LanguageTracker,
    chunk_for_tts,
    contains_phrase,
    detect_script_lang,
)
from voice.core.vad import SpeechSegmenter
from voice.plugins import base as plugins

DEFAULT_BASE_URL = "http://127.0.0.1:5055"


def ask_aali(
    message: str,
    sid: str,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = "",
    timeout_s: float = 180.0,
) -> Dict[str, Any]:
    """POST /api/ask -> {ok, reply?, sid?} (the stable house API contract)."""
    payload = json.dumps({"message": message, "sid": sid}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/ask",
        data=payload,
        headers={
            "Content-Type": "application/json",
            **({"X-API-Key": api_key} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 404 is the house "you are not authorized" answer (uniform-404 contract),
        # so a bare traceback here would be a dead end for the operator.
        if exc.code in (401, 403, 404):
            raise RuntimeError(
                f"aali refused the ask (HTTP {exc.code}): {base_url} runs in KEY MODE — "
                "set AALI_VOICE_API_KEY (or AALI_API_KEY) to its master key"
            ) from exc
        raise RuntimeError(f"aali ask failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"aali unreachable at {base_url}: {exc.reason}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"aali ask failed: {data.get('error', 'unknown')}")
    return {"reply": str(data.get("reply", "")), "sid": data.get("sid", sid)}


class VoicePipeline:
    def __init__(
        self,
        segmenter: SpeechSegmenter,
        stt: Any,
        tts: Any,
        ask_fn: Callable[..., Dict[str, Any]] = ask_aali,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        barge_in: Optional[BargeInGate] = None,
        sid_prefix: str = "voice-",
        wake_phrase: str = "",
    ):
        self.segmenter = segmenter
        self.stt = stt
        self.tts = tts
        self.ask_fn = ask_fn
        self.base_url = base_url
        self.api_key = api_key or ""
        self.barge_in = barge_in or BargeInGate()
        self.sid_prefix = sid_prefix
        self.wake_phrase = (wake_phrase or "").strip()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.lang_tracker = LanguageTracker()

    # -- session state -----------------------------------------------------
    def session_state(self, sid: str) -> Dict[str, Any]:
        st = self.sessions.get(sid)
        if st is None:
            st = {
                "sid": sid or f"{self.sid_prefix}{uuid.uuid4().hex[:10]}",
                "tts_speaking": False,
                "barge_pending": False,
                "last_lang": self.lang_tracker.current,
            }
            self.sessions[st["sid"]] = st
        return st

    # -- audio in ------------------------------------------------------------
    def feed_audio(self, pcm16, sid: str = "") -> List[Dict[str, Any]]:
        """Stream 16k int16 PCM; returns completed speech segments.

        While Aali is speaking, segments are suppressed here — the SERVER
        layer decides barge-in (cancel + queue), so the pipeline never
        processes an interrupting segment as a new turn by accident.
        """
        state = self.session_state(sid)
        segs = self.segmenter.feed(pcm16)
        return [] if state["tts_speaking"] else segs

    # -- turn ------------------------------------------------------------------
    def run_turn(
        self,
        seg: Dict[str, Any],
        sid: str = "",
        on_chunk: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """One full turn: STT -> Aali -> chunked TTS.

        on_chunk fires per TTS chunk result so the server can stream audio
        out as soon as the first chunk is ready (perceived latency).
        """
        state = self.session_state(sid)
        stt_res = self.stt.transcribe(seg["pcm"], seg.get("sample_rate", 16000))
        text = (stt_res.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "empty transcription", "sid": state["sid"]}
        self.lang_tracker.update(stt_res.get("lang"), float(stt_res.get("confidence", 0.0)))

        # Plugin hook: a VERTICAL may answer this turn from its own data
        # without spending an LLM call. Absent/None -> normal path below.
        meta: Dict[str, Any] = {"sid": state["sid"], "lang": stt_res.get("lang")}
        direct = plugins.apply_turn_start(text, stt_res.get("lang") or "ar", meta)
        if direct:
            text = plugins.apply_transcript(text, stt_res.get("lang") or "ar", meta)
            direct = plugins.apply_reply(direct, meta)
            chunks = chunk_for_tts(direct)
            items = []
            state["tts_speaking"] = True
            try:
                for chunk in chunks:
                    chunk_lang = detect_script_lang(chunk) or stt_res.get("lang") or "ar"
                    r = self.tts.synthesize(chunk, chunk_lang)
                    item = {"text": chunk, "lang": chunk_lang,
                            "ok": bool(r.get("ok")), "path": r.get("path"),
                            "sr": r.get("sr"), "engine": r.get("engine")}
                    if not item["ok"]:
                        item["error"] = r.get("error", "tts failed")
                    items.append(item)
                    if on_chunk:
                        try:
                            on_chunk(item)
                        except Exception:
                            pass
            finally:
                state["tts_speaking"] = False
            items = plugins.apply_chunks(items)
            result = {
                "ok": all(c["ok"] for c in items) if items else False,
                "sid": state["sid"], "user_text": text,
                "user_lang": stt_res.get("lang"), "reply": direct,
                "chunks": items, "partial": any(not c["ok"] for c in items),
                "handled_by": meta.get("handled_by"),
            }
            plugins.apply_turn_done(result)
            return result

        # Wake word: a turn that does not contain the phrase is heard but NOT
        # answered. Honest cost note: this is a FILTER over an utterance we
        # already transcribed, not a low-power wake engine — it saves the
        # LLM call, not the STT one.
        if self.wake_phrase and not contains_phrase(text, self.wake_phrase):
            return {
                "ok": True, "ignored": True, "sid": state["sid"],
                "user_text": text, "user_lang": stt_res.get("lang"),
                "reply": "", "chunks": [], "partial": False,
                "reason": f"wake phrase {self.wake_phrase!r} not present",
            }

        try:
            ask = self.ask_fn(
                message=text,
                sid=state["sid"],
                base_url=self.base_url,
                api_key=self.api_key,
            )
        except Exception as exc:  # honest turn failure, never a traceback
            return {"ok": False, "error": str(exc) or type(exc).__name__,
                    "sid": state["sid"], "user_text": text}
        reply = (ask.get("reply") or "").strip()
        reply = plugins.apply_reply(reply, meta) or reply
        if not reply:
            return {"ok": False, "error": "empty reply from Aali", "sid": state["sid"],
                    "user_text": text}

        chunks = chunk_for_tts(reply, max_chars=240)
        out_chunks: List[Dict[str, Any]] = []
        state["tts_speaking"] = True
        try:
            for chunk in chunks:
                chunk_lang = detect_script_lang(chunk) or stt_res.get("lang") or "ar"
                r = self.tts.synthesize(chunk, chunk_lang)
                item = {
                    "text": chunk,
                    "lang": chunk_lang,
                    "ok": bool(r.get("ok")),
                    "path": r.get("path"),
                    "sr": r.get("sr"),
                    "engine": r.get("engine"),
                }
                if not item["ok"]:
                    item["error"] = r.get("error", "tts failed")
                out_chunks.append(item)
                if on_chunk:
                    try:
                        on_chunk(item)
                    except Exception:
                        pass
        finally:
            state["tts_speaking"] = False
        state["last_lang"] = self.lang_tracker.current
        out_chunks = plugins.apply_chunks(out_chunks)
        result = {
            "ok": all(c["ok"] for c in out_chunks) if out_chunks else False,
            "sid": state["sid"],
            "user_text": text,
            "user_lang": stt_res.get("lang"),
            "reply": reply,
            "chunks": out_chunks,
            "partial": any(not c["ok"] for c in out_chunks),
            "handled_by": meta.get("handled_by"),
        }
        plugins.apply_turn_done(result)
        return result

    # -- status ---------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "sessions": len(self.sessions),
            "language": self.lang_tracker.current,
            "wake_phrase": self.wake_phrase,
            "tts_engine": self.tts.engine_report() if hasattr(self.tts, "engine_report") else {},
        }


import numpy as np  # noqa: E402  (bottom import keeps the docstring first)
