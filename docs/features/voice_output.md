# المخرجات الصوتية — Voice Output / Piper TTS (Wave 1 #3)

_Shipped 2026-09-25 · owner-approved Wave 1 · closes the "voice output"
gap from the Part 9.1 audit — Aali can now SPEAK his replies, fully
offline._


## What it does

Every assistant reply in the web UI gets a 🔊 button: click and Aali
speaks the reply in Arabic (Piper, local, no cloud). Plus two API
endpoints for any client.

## Architecture (dependency isolation)

- **Piper 1.8** lives ONLY in `D:/hwk-tools/vision-venv` (isolated,
  gitignored) — the training/server venv gained ZERO new deps.
- The server calls the isolated venv's python as a **subprocess**
  (`file_agent/tts.py::_CHILD_SCRIPT`); text crosses via stdin as one
  JSON object (no argv-escaping bugs), WAV comes back to a temp file,
  gets header-validated, then atomically replaces the cache entry.
- Voices: `D:/hwk-data/voices/*.onnx` + `.onnx.json` sidecars.
  Installed: **ar_JO-kareem-medium** (Arabic, male, medium quality).
  More voices = drop files in the folder; Arabic sorts first.
- Cache: `D:/hwk-data/voices/cache/<sha256(voice|speed|text)>.wav` —
  identical requests are instant.
- Log (content-free): `D:/hwk-data/tts.log` — voice, char count, bytes,
  hit/miss. Never the text itself.

## API

`POST /api/voice/synthesize` — body `{text, voice?, speed?}` →
`audio/wav` (headers `X-Aali-Voice`, `X-Aali-Cached`).
- `speed` 0.5–2.0 (clamped; 1 = normal; implemented via Piper's
  `length_scale = 1/speed`).
- text ≤ 1000 chars; empty → 400; unknown voice → 503-family error
  JSON; synthesis failure → 503.
- Permission `tts` (owner/dev/admin/user — NOT guests; guests get the
  uniform 404 per Track B 11.2; TTS exposes no conversation data but
  costs CPU, so it's user+).

`GET /api/voice/voices` — `{available, default, voices[]}`.

## Web UI

🔊 button in each assistant reply's action row (next to copy/regenerate).
`speak()` fetches the WAV as a blob (API key never in a URL), plays via
Web Audio; pressing again stops + re-speaks; settings (voice/speed/
auto-play) read from localStorage in a later polish pass — API already
accepts them.

## The 2026-09-25 incident (pinned lesson)

An early buggy test fake wrote its output WAV onto **the real 63MB
voice model** (wrong argv index while the voices-dir fixture was
missing on endpoint tests) — ONNX became a 4KB WAV, ONNXRuntime failed
`INVALID_PROTOBUF`. Fixed twice over:
1. `tests/test_tts.py` now has an **autouse sandbox fixture**: every
   test in the file runs against a disposable voices dir — the real
   `D:/hwk-data/voices` is unreachable from the suite, forever.
2. `tts.synthesize` validates the WAV header (frames > 0) before
   caching/serving — a broken model can never poison a served file.

## Tests

`tests/test_tts.py` (19): voice discovery + Arabic-first ordering,
default-voice pick, availability checks, empty/overlong/unknown-voice
rejection, speed clamping + separate cache entries, cache hit, invalid
WAV rejection, subprocess failure path, endpoint contract (owner 200
audio/wav, empty 400, failure 503, remote-unauthenticated 404,
authenticated remote user 200, voices listing). Live smoke verified
out-of-suite: real synthesis through the running server = HTTP 200,
45,100-byte RIFF WAV.
