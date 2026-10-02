# Aali Voice Agent — Phase 1 (core voice loop)

**Status: Phase 1 core COMPLETE (2026-10-02). AR/HE/EN, all local, CPU-first,
isolated venv. Commercial verticals are NOT built (Phase 1 scope only).**

## What it is
Real-time voice conversation with Aali: mic → VAD → Whisper STT → Aali
(:5055) → sentence-chunked TTS with a cloned Arabic voice → streamed audio
back over WebSocket. Arabic-first, Hebrew + English first-class,
code-switching handled per SENTENCE (each sentence speaks in its own tongue).

## Layout
```
scripts/voice/
  core/{vad,stt,language_detect,tts,pipeline,barge_in,server,cli}.py
  plugins/base.py        # interface ONLY (no plugin yet, by design)
  config.yaml
  probe_xtts.py          # [1.1] gate: model fetch + voice-cloned synth
  fetch_whisper.py       # pre-downloads STT model as plain files
web/voice/               # Arabic-first RTL mic UI (mic → WS → playback)
```

## Stack (all local, no cloud)
| stage | engine | notes |
|---|---|---|
| VAD | Silero (ONNX, CPU) | 32ms frames, pre-roll 380ms, close after 700ms silence |
| STT | faster-whisper large-v3-turbo int8 CPU | local dir D:/hwk-models/faster-whisper-large-v3-turbo |
| LLM | Aali :5055 (`/api/ask`) | key from AALI_VOICE_API_KEY / AALI_API_KEY; sessions `voice-*` |
| TTS | XTTS-v2 (voice-clone) + Piper fallback | refs in D:/hwk-models/voice/references/ |

## Phase 2 — voice asset studio (2026-10-02)
```
scripts/voice/core/voices.py   # voice library: profiles + validation
scripts/voice/studio.py        # CLI: voices | audition | batch
scripts/voice_studio.bat       # double-click launcher
```
- **Profiles**: name / language / reference wav / note, stored at
  D:/hwk-data/voice/voices.json; the clip is copied into the managed
  references dir, and each language has one default voice.
- **Validation before admission**: mono, 16-bit, ≥16kHz, 4–30s, level,
  clipping — refused with the real reason, because a bad clip clones into
  a robotic voice.
- **Audition** a voice; **batch** a plain text script (blank line =
  utterance) into numbered wavs + `manifest.jsonl`, resuming a partial run.

### Hebrew is not an XTTS language (verified, not assumed)
XTTS-v2 ships 17 languages (en es fr de it pt pl tr ru nl cs ar zh-cn hu ko
ja hi) — **no Hebrew**. The first real batch failed with
`NotImplementedError: Language 'he' is not supported.`; Phase 1's claim of
AR/HE/EN XTTS support was wrong.

Hebrew now routes to Piper with the installed `he_IL-saspeech-medium`
voice, matched by language (`_piper_voice_for`) — never the house default,
which would read English/Hebrew in the Arabic voice. Measured: 2.7s per
Hebrew line vs 20–160s for an XTTS line on this CPU.
`engine_report().lang_support` states the truth per language.

## Voice cloning
XTTS-v2 in D:/hwk-models/xtts-v2 (plain files, CCCPML license accepted by
the owner's brief). AR ref `arabic_male.wav` exists; HE/EN refs are
optional — until recorded, ALL languages clone the AR voice (honest note;
no fabrication of a "native" voice).

## Language handling
- `detect_script_lang` — deterministic script detection per SENTENCE.
- `chunk_for_tts` — sentence-first chunks; a script CHANGE always starts a
  new chunk, so code-switched replies speak each sentence correctly.
  `ENGINE_CHAR_CAPS` bounds each chunk by the ENGINE's tokenizer window
  (ar/he 160, en 280) — XTTS silently TRUNCATES past it, so the smaller
  per-language cap always wins over the caller's max_chars.
- `LanguageTracker` — majority-vote smoothing across utterances (default ar).
- Whisper's per-utterance detection can be overridden by the transcript's
  script when its confidence is low (Hebrew/Arabic confusion guard).

## Transport
- WS :5080 — client streams 16k int16 PCM; server emits events
  (hello/transcript/reply/chunk_start/turn_done/barge_in/error) + raw 24k
  int16 audio frames per chunk.
- Static UI :5081 (web/voice/). Loopback by default; AALI_VOICE_BIND=0.0.0.0
  to expose (same fail-safe convention as the main server).
- Barge-in (Phase 1 honest version): user audio WHILE chunks stream cancels
  the remaining chunks; the interrupting segment becomes the next turn.
  No AEC yet — see limits.

## Run
```
D:/hwk-tools/voice-venv/Scripts/python.exe scripts/voice/server.py   # servers
D:/hwk-tools/voice-venv/Scripts/python.exe scripts/voice/cli.py      # live mic
D:/hwk-tools/voice-venv/Scripts/python.exe scripts/voice/cli.py --file x.wav --save out.wav

scripts\voice_studio.bat voices                                    # what can speak what
scripts\voice_studio.bat voices check clip.wav                      # validate a recording
scripts\voice_studio.bat voices add my_voice --lang ar --file clip.wav
scripts\voice_studio.bat audition --lang he
scripts\voice_studio.bat batch my_script.txt                        # -> D:/hwk-data/voice_out/<name>/
```
**AALI_VOICE_API_KEY is required when :5055 runs in key mode** (it does —
master key in D:/hwk-data/aali_master_key.txt). Without it the house
uniform-404 contract answers every ask; `ask_aali` now says exactly that
instead of surfacing a bare HTTPError.

Config: scripts/voice/config.yaml. TTS cache: D:/hwk-data/voice_tts/
(sha1 of engine|lang|ref|text). Logs: D:/hwk-data/voice_tts.log,
voice_probe.log, voice_e2e.log (content-free).

## Honest limits (Phase 1)
- CPU TTS first chunk ≈ 20-25s (RTF ~4-6x). Natural > fast per the brief;
  streaming + short first sentences mask part of it. GPU later = ~10x.
- Hebrew is a PIPER voice, not a clone: it is a different, flatter voice
  than the Arabic XTTS clone. Cloning a Hebrew reference would need an
  engine that supports Hebrew — none is installed locally.
- No echo cancellation: barge-in uses VAD gating; Aali's own playback may
  trigger it. Wired headphones recommended.
- Whisper hallucinates on silence (classic whisper failure) — the VAD
  segmenter filters most of it; min_speech_ms drops coughs/clicks.
- HE/EN refs not recorded → AR voice speaks HE/EN (clone works cross-lingual
  but the accent is AR).
- No wake word, no streaming STT (utterance-level only), single speaker.

## Live chain (verified 2026-10-02)
Real XTTS → real Whisper → real Aali :5055 → real XTTS, CPU:
`D:/hwk-data/voice_e2e.log` — AR spoken line transcribed back correctly
(`lang=ar`), Aali replied in Arabic, one AR chunk synthesized by the cloned
voice, 39.4s turn wall cold (0s cached). Cold XTTS synth ≈ 26s (49 chars)
/ 63s (180 chars) on CPU — hence the engine char cap above.

## Tests
tests/test_voice_agent.py — 42 tests: language detect/chunking (incl. the
engine char cap), tracker, segmenter state machine, barge-in gate, STT
gating (fake whisper), TTS seams + cache + XTTS language set + Hebrew
routing + language-matched Piper voice, the voice library (validation of
every real defect, CRUD, defaults, named-voice isolation), pipeline turn
e2e (fakes) + honest ask failure, ask_aali contract + key-mode 404
(hermetic HTTP server), UTF-8 stdio guards, WS e2e with the FAKE pipeline
(skips where websockets is absent).
Models are NEVER loaded by the suite; the real-model gate is probe_xtts.py,
the CLI --file run and `studio batch` (see D:/hwk-data/voice_e2e.log and
D:/hwk-data/voice_out/).
