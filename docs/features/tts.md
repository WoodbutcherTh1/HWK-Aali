# النطق — TTS / Piper Voice Output (Wave 1 #3, full contract)

_Shipped 2026-09-25 · commits 203d6c2 + ff2f6e9 · 100% local, no cloud._


## What it does

Aali **speaks** his replies. Speaker button (🔊) on every assistant
message, auto-play toggle, voice/speed/volume settings, CLI voice
commands — all powered by Piper running locally on this PC.

## Architecture

```
browser/CLI ──POST /api/voice/synthesize──▶ Flask (.venv)
        ◀─────────── audio/wav|mp3 ────────   │ subprocess (text via
                                              │ stdin JSON, no args-escape)
D:/hwk-models/piper/*.onnx ◀── piper 1.8 ─── D:/hwk-tools/vision-venv
                                              (ISOLATED — no new deps in
                                               server/training venv)
cache: D:/hwk-data/tts_cache/<sha256(voice|speed|text)>.wav|.mp3
log:   D:/hwk-data/tts.log (content-free: voice/chars/bytes only)
```

- WAV natively; MP3 via the machine's ffmpeg — when ffmpeg is absent
  the server serves WAV with `X-Aali-Note` (honest fallback).
- WAV header validated before caching/serving.
- The child's stderr tail lands in tts.log on failure (the 12:11 lesson:
  never let the real error sit in a pipe nobody reads).

## Voices (D:/hwk-models/piper/)

| id | language | quality | notes |
|---|---|---|---|
| ar_JO-kareem-low | ar_JO | low (16 kHz) | fast fallback |
| ar_JO-kareem-medium | ar_JO | medium (22 kHz) | default quality |

Default = first Arabic voice alphabetically. Add voices by dropping
`*.onnx` + `.onnx.json` into the folder. Arabic sorts first everywhere.

## API contract

- `POST /api/voice/synthesize` `{text, voice?, speed?, format?}`
  → `audio/wav` or `audio/mpeg`; headers `X-Aali-Voice`,
  `X-Aali-Format`, `X-Aali-Cached` (+`X-Aali-Note` on fallback).
  - speed 0.5–2.0 (clamped), text ≤ 2000 chars (guest cap 500 —
    `GUEST_TEXT_CHARS`, applied per role server-side)
  - 400 empty · 413 too long · 404 unknown voice / unauthorized caller
    (Track B uniform-404) · 429 rate limit (30/min per caller) ·
    503 synthesis failure
  - permission `tts`: owner/dev/admin/user — guests excluded
- `GET /api/voice/list` (alias `/api/voice/voices`) →
  `{available, default, voices:[{id,name,language,gender,quality,arabic}]}`

## UI usage

- 🔊 on each reply: click to synthesize+play, ⏹ while playing, ◌ while
  synthesizing; failure → toast with retry.
- Settings ⚙︎ → الصوت: auto-play toggle ("قراءة الردود"), speed slider,
  volume slider, voice picker with per-voice ▶ preview.
- Auto-play reads every new reply after streaming ends (default OFF).
- Persisted in localStorage (`aali_voice`). iOS Safari: playback needs a
  user gesture — auto-play may stay silent until the first tap.
- The desktop app (WebView2) shares the web UI; audio supported.

## CLI usage

```
/voice status          # auto/voice/speed
/voice on | off        # auto-play toggle (reads every reply in the REPL)
/voice list            # installed voices (Arabic first, ← marks current)
/voice set <id>        # switch voice
/voice speed 1.3       # 0.5–2.0
/say <text>            # synthesize + play immediately (winsound/afplay)
```
Persisted in `~/.aali_cli_voice`.

## Settings reference

| Setting | Where | Default |
|---|---|---|
| auto-play | web settings / `/voice on\|off` | OFF |
| voice | web settings / `/voice set` | first Arabic voice |
| speed | web settings / `/voice speed` | 1.0 |
| volume | web settings (0–100) | 100 |
| notifications sounds | (TTS endpoint errors toast only) | — |

## Tests

`tests/test_tts.py` (29): voice discovery + quality parsing +
Arabic-first order, default pick, availability, empty/overlong/unknown
rejections, cache hit + per-speed variants, speed clamping, invalid WAV
rejection, mp3 success (faked ffmpeg) + honest WAV fallback, endpoint
contract (200 audio, 400/413/429/404/503, remote user 200, remote
anonymous 404, listing + alias), zero-chunk honest rejection +
`_CHILD_SCRIPT` tripwire. Suite: 951 passed / 9 skipped.
Live: 8/8 consecutive synths HTTP 200 after the true restart.

## Zero-chunk incident (2026-09-25, fixed in commit 529b39e)

Some texts made the child die with `wave.Error: '# channels not
specified'` (rc=1): when espeak-ng phonemizes a text to NOTHING, piper
yields ZERO chunks, its `set_wav_format` never runs, and the `with`
block's `close()` blows up. The real trigger was client-side mojibake —
shell-encoded curl bodies mangled the Arabic (`chars=27` in the log for
an 18-char text), and un-phonemizable garbage produces no phonemes —
direct clean runs always passed. Fix: the child pre-sets the WAV format
itself (mono / 16-bit / voice sample_rate) and calls
`synthesize_wav(..., set_wav_format=False)` inside a `finally`-close, so
a no-chunk run closes cleanly as a 0-frame WAV and the PARENT rejects it
honestly (size/frame validation → 503 "synthesis failed", never cached,
never served). Verified live: the previously-failing text returns
HTTP 200 + real audio, and punctuation-only/un-phonemizable input gets a
clean honest 503.
