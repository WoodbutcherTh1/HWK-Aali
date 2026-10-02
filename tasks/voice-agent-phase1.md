# Aali Voice Agent — Phase 1 (core voice loop)

- owner: buffy (this PC, Freebuff session)
- status: done — PART 1 of the owner's 3-part package (2026-10-02); live
  chain verified end to end (see Log). Parts 2/3 need a fresh owner go.
- started: 2026-10-02
- owner GO: explicit — "START with PART 0 now", continue to PART 1.
  STOP after each PART; Parts 2 (Studio) and 3 (Reach) wait for a fresh go.

## Mission
Real-time natural voice conversation for Aali in AR (Jordanian/Palestinian)
+ HE + EN with code-switching. Phase 1 = core loop only (no verticals).
Commercial direction (local businesses) is noted but NOT built in Phase 1.

## Hard rules (from the owner's brief)
1. NO cloud TTS — all local. 2. CPU-first (GPU only if XTTS truly needs it —
brief says CPU-only, honor that). 3. Isolated venv D:/hwk-tools/voice-venv.
4. Natural voice > speed. 5. AR+HE+EN first-class. 6. Code-switching.
7. Cultural neutrality. 8. Plugin-extensible design. 9. Honest about limits.
10. No robotic voices.

## Stack
- VAD: Silero VAD (CPU)          - STT: faster-whisper large-v3-turbo
- LLM: Aali :5055 (checkpoint-3933 served on :20129; API routes it)
- TTS: Coqui XTTS-v2 primary (voice refs D:/hwk-models/voice/references/,
  arabic_male.wav exists) + Piper fallback (already in vision-venv)
- Transport: WebSocket + PCM on :5080

## Layout
scripts/voice/core/{vad,stt,language_detect,tts,pipeline,barge_in,server,cli}.py
scripts/voice/plugins/base.py (interface only) + config.yaml
web/voice/{index.html,app.js,style.css}

## Log
- 2026-10-02 [1.1] started: PART 0 reported (Hebrew 3.5 COMPLETE incl.
  Knesset 2.24 GB; C: regressed to 47G free — flagged; RAM 4.5/15.9 free —
  tight, watch XTTS load). venv creation next.
- 2026-10-02 [1.1] VENV+XTTS DONE: D:/hwk-tools/voice-venv (Py 3.12.10);
  coqui-tts 0.27.5 (fork; original repo archived) + torch/torchaudio 2.8.0
  CPU (2.9+ requires torchcodec -> DLL hell with ffmpeg 9; downgraded) +
  transformers<5 pin (5.x removed isin_mps_friendly -> import error).
  XTTS-v2 = coqui/XTTS-v2, NOT gated (API-verified), fetched with
  local_dir= (HF cache symlinks die on this box: WinError 1314, no
  Developer Mode) -> D:/hwk-models/xtts-v2 (2.0 GB plain files); stale
  2.0 GB C: cache purged. FIRST AR voice-clone synth OK (22.6s CPU warm,
  100s incl. load); EN OK via AR-voice cross-lingual clone (92.1s).
  Fixes on the way: TTSApi(model_path) cannot dispatch XTTS config in the
  fork -> direct Xtts API (config + load_checkpoint + get_conditioning_latents
  cached per ref); _ref_for now FALLS THROUGH when a per-lang ref is
  missing (EN probe initially killed by absent english_male.wav);
  wavs saved PCM_16 via soundfile (torchaudio float32 wav breaks wave +
  streaming).
- 2026-10-02 [1.2-1.5] CORE MODULES DONE: vad.py (SpeechSegmenter pure
  state machine + SileroVAD; silero-vad 6.x returns the model directly —
  5.x tuple unpack was the e2e kill), language_detect.py (script detect /
  sentence split / language-aware chunk_for_tts — script CHANGE starts a
  new chunk so code-switched replies speak per-sentence; LanguageTracker
  majority vote), barge_in.py (sustained-speech gate + cooldown), stt.py
  (WhisperSTT with script-override on low-confidence misdetects;
  model_dir param — SYSTRAN has NO turbo repo, the real one is
  deepdml/faster-whisper-large-v3-turbo-ct2 MIT, fetched to
  D:/hwk-models/faster-whisper-large-v3-turbo), tts.py (XTTS primary +
  file_agent.tts piper fallback per-language-honest, sha1 cache
  D:/hwk-data/voice_tts, content-free voice_tts.log), pipeline.py
  (VoicePipeline: segment -> STT -> ask_aali :5055 -> chunked TTS with
  on_chunk streaming; everything injectable).
- 2026-10-02 [1.7-1.8] SERVER+UI DONE: server.py (WS :5080 binary PCM in /
  JSON events + 24k PCM out; static :5081; loopback default;
  AALI_VOICE_FAKE seam; barge-in = audio WHILE chunks stream cancels the
  rest, interrupting segment becomes the next turn — first version had a
  self-cancel race the e2e test caught) + web/voice/ Arabic-first RTL UI
  (mic capture 16k, WS, streaming playback, level meter, latency line).
- 2026-10-02 [1.9-1.10] TESTS+CLI DONE: tests/test_voice_agent.py 24
  (logic tests green in BOTH venvs; WS e2e runs in voice-venv;
  models never loaded by the suite) — full repo suite 1237 green / 13
  skipped. cli.py: --file (any SR, honest resample, float32-wav tolerant)
  / --fake / live mic mode; scripts/voice/chain_e2e.py drives the REAL
  chain (VoiceTTS -> WhisperSTT -> Aali -> TTS) as a library.
- 2026-10-02 plugins/base.py: interface ONLY (hooks + registry), per the
  extensible-design rule; no plugin yet.
- 2026-10-02 LIVE CHAIN DONE (voice_e2e.log, real models end to end):
  XTTS AR line -> Whisper transcribed it back correctly (lang=ar) -> Aali
  :5055 replied in Arabic -> cloned AR voice synthesized the chunk; 39.4s
  turn wall cold. THREE defects found + fixed by the live run:
  (1) cp1252 stdout killed the chain at the FIRST Arabic print
  (UnicodeEncodeError) — shared voice.force_utf8_stdio() now called by
  cli.py + chain_e2e.py, same guard the rest of the repo uses (test:
  cp1252 stream + source tripwire);
  (2) :5055 runs in KEY MODE, so ask_aali got the house uniform-404 and
  raised a bare HTTPError — ask_aali now names the KEY MODE cause
  (AALI_VOICE_API_KEY / AALI_API_KEY) and run_turn returns an honest error
  DICT (never a traceback) with the transcript it heard; chain_e2e honors
  AALI_API_KEY too;
  (3) XTTS warns+TRUNCATES past its tokenizer window (166 chars ar/he) and
  chunk_for_tts packed up to 240 — added ENGINE_CHAR_CAPS {ar:160, he:160,
  en:280}, the per-language cap always wins over max_chars.
  Tests 24 -> 30; voice file 27 passed / 3 skipped; FULL suite 1243 green /
  13 skipped.
- Phase 1 = DONE. Parts 2 (Studio) and 3 (Reach) wait for the owner's go.
