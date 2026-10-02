# Aali Voice — Phase 2 (Studio → Console → Quality → Verticals)

- owner: buffy (this PC, Freebuff session)
- status: in-progress — slice 1 (voice asset studio) DONE and verified live;
  slices 2-4 open
- started: 2026-10-02
- owner GO: "Start Part 2 of the voice package (Studio)" with ALL FOUR
  areas selected (voice assets, script-to-voice console, voice quality,
  commercial verticals) and no single priority — so the slices are SEQUENCED
  below, each one usable on its own.

## Why this order
1. **Voice assets** first: the console needs voices to choose between, and
   Hebrew turned out to be the honest blocker (see slice 1 finding).
2. **Console** next: it is the same library over HTTP + a web page.
3. **Quality** (echo cancellation, streaming STT, wake word) is the biggest
   engineering risk and the least useful until there are voices.
4. **Verticals** last: they are plugins (the Phase 1 plugin interface is
   already there) and must not distort the core loop.

## Hard rules (unchanged from Phase 1)
NO cloud TTS · CPU-first · isolated venv D:/hwk-tools/voice-venv ·
AR+HE+EN first-class · natural voice > speed · cultural neutrality ·
honest about limits.

---

## Slice 1 — voice asset studio (DONE 2026-10-02)

`scripts/voice/core/voices.py` (library) + `scripts/voice/studio.py` (CLI)
+ `scripts/voice_studio.bat` (double-click launcher).

- Profiles: name / language / reference wav / note / created, stored at
  D:/hwk-data/voice/voices.json; the clip is COPIED into the managed
  references dir so a profile is never a dangling path.
- One DEFAULT voice per language; deleting a voice re-points the default,
  never leaves a language pointing at nothing.
- Reference clips are VALIDATED before admission (mono, 16-bit, ≥16k,
  4-30s, level, clipping) and refused with the real reason — a bad clip
  clones into a robotic voice.
- Audition a voice; `batch SCRIPT.txt` turns a plain text script (blank
  line = utterance) into numbered wavs + manifest.jsonl, RESUMING (a re-run
  only synthesizes what is missing).
- VoiceTTS.synthesize(text, lang, voice=NAME): a named voice that does not
  exist fails LOUDLY instead of silently speaking with the per-language
  reference (the Arabic-accent trap).

### The finding that mattered: XTTS-v2 CANNOT SPEAK HEBREW
The first real batch run failed on the Hebrew line:
`NotImplementedError: Language 'he' is not supported.` Verified against the
LOCAL config.json: XTTS-v2 ships 17 languages (en es fr de it pt pl tr ru
nl cs ar zh-cn hu ko ja hi) — Hebrew is not among them, and Phase 1's
docstring claiming AR/HE/EN support was simply wrong.

Second honesty bug found in the same pass: the Piper fallback passed
`voice_id=None`, so ANY non-Arabic fallback line would have been read in
the ARABIC voice while the docstring claimed it refused. Now
`_piper_voice_for(lang)` matches the voice by language and refuses honestly
when nothing matches.

Fix shipped: `he_IL-saspeech-medium` (Piper, 63 MB, fetched from
rhasspy/piper-voices) installed in D:/hwk-models/piper. Hebrew now speaks,
through the language-matched Piper path (2.7s per line vs XTTS's 20-160s on
CPU). `engine_report().lang_support` reports the truth per language:
ar→xtts, he→piper, en→xtts.

### Verified live (not just tests)
- `voices check` on the owner's real clip: 10.0s · 22050Hz · rms 0.0224 · accepted.
- `voices add arabic_male --lang ar` → registered + default.
- `batch` on scripts/voice/samples/demo_ar.txt → 4/4 lines: 2 AR + 1 HE + 1 EN,
  all real audio (rms 0.11-0.15), manifest written, re-run reused 3 (resume works).

### Tests
tests/test_voice_agent.py: +15 (39 passed / 3 skipped; full suite below).
Covers the XTTS language set, Hebrew never reaching XTTS, the
language-matched Piper voice, library CRUD/defaults/deletion, clip
validation of every real defect, named-voice isolation, corrupt store.

---

## Slice 2 — script-to-voice console (web, Arabic-first)
Not started. Plan: HTTP endpoints over the same library (list voices,
validate+upload a clip, audition, synthesize, download), served next to the
existing :5081 voice UI; UI = write text, pick voice/language, preview,
export. Arabic-first RTL per the house rule.

## Slice 3 — voice quality
Not started. Echo cancellation (today barge-in can trip on Aali's own
playback), streaming/partial STT, wake word. Honest: these decide whether
conversation feels natural.

## Slice 4 — commercial verticals (local businesses)
Not started. Built as voice PLUGINS over the existing interface
(scripts/voice/plugins/base.py), not by forking the core loop.