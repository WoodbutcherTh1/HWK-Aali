# Teacher distillation → scratch-shaped SFT rows (Phase C continuation, lever b)

- owner: buffy (this PC, Freebuff session)
- status: in-progress
- started: 2026-09-26

## Why
Run-8 verdict (tasks/phase-c-own-brain-sft-relaunch.md): the scratch row shape
is now correct but the 110M base cannot compose fluent prose. Owner chose
lever (b): distill the PROMOTED teacher (checkpoint-3873 serving on :20129)
into clean scratch-shaped rows so the own-brain SFT learns from a strong
completion signal instead of the small-adapter-targeted sft_v2 mix.

## Contract (single sources of truth, never re-implemented)
- Row rendering: train_scratch._sft_prompt_text / _sft_record_text
  (System: You are Aali. → Available tools: … → turns → SCRATCH_INSTRUCTION →
  "Assistant:").
- Teacher request: the RENDERED scratch prompt (single user message, the
  soup_exam.py pattern) so the teacher answers in the same shape the student
  is asked — run-7 lesson: shapes must match.
- Token budgets: the v2 estimator; scratch constants answer ≤200, total ≤900.

## Quality gates (drop + name, never silent)
- L1 self-leak: any 8-word shingle of the seed prompt in the reply.
- L2 canaries: XMARK-7731 / LEXSEAL-5219 / GLINTQUILL-3407 in a reply.
- Exam leakage: seed prompts colliding with exam user prompts (hash or
  8-word shingle) fail the build loudly.
- Tool JSON: registry-or-alias names only (parse-and-redump), required
  schema args present; final envelopes unwrapped to plain text.
- Language: AR seeds must get Arabic replies, EN seeds Latin-only replies.
- Budget: token_report answer/total within the scratch constants.
- Identity seeds NEVER hit the teacher — deterministic _identity_fast card.

## Outputs
- D:/hwk-data/distill/sft_distill.jsonl (+ .report.json, content-free
  counters only). Trainer consumes via AALI_PHASE_C_DATA at run 9 — launch
  stays a human decision.

## Scope decisions (owner direction 2026-09-26)
- v1 is single-turn (seed → teacher answer); mid-course tool-result episodes
  are NOT dropped later — dropping happens only at write time, named in the
  report. Multi-turn distillation is future work.

## LIVE RUN 1 (2026-09-26, ungated eyeball pass — 60 seeds → 11 rows)
First pass caught by the eyeball test, not the gates: the teacher answered
plain chat with fenced tool JSON (```json …``` slipped the plain-text path),
a refusal seed was answered WITH a run_command env-dump call (compliance
poison nearly trained), and chat/capability seeds got placeholder-argument
tool calls. All 11 rows archived as sft_distill.v1-ungated.jsonl (never
to be trained on).

## Gate hardening (same day, tests 24 → 33)
- _FENCE_RE unwraps markdown-fenced replies before judging (fenced tool JSON
  hits the same schema/name gates; fenced prose judged as prose).
- Kind-aware acceptance: tool-call answers valid only for tool/memory/honesty
  kinds; chat/capability → drop `tool-for-chat`; refusal + tool call →
  drop `refusal-complied` (named loudly).
- Refusal cue gate: a refusal seed's plain answer must carry an actual AR/EN
  refusal cue (`refusal:no-cue` otherwise) — compliant prose never trains.

## LIVE RUN 2 (2026-09-26, hardened gates — 60 seeds → 5 rows)
4 identity cards (deterministic, teacher-independent) + 1 plain chat row.
Drops: 43 schema (teacher's tool JSON rarely carries required args),
7 self-leak (prompt parroting), 2 refusal-complied, 2 tool-for-chat,
1 unknown tool name. Yield from the teacher: **1 usable row in 56 asks**.

## VERDICT — the promoted teacher is NOT a viable distillation source
checkpoint-3873's generation quality (3/26 exam, weak arg discipline,
prompt-parroting, weak security-refusal discourse) makes lever (b) as
specified a dead end at any useful scale. Consistent with its exam
breakdown (media 0/5, security 0/4). NOTE: the 2 refusal-compliances are a
live-brain observation — at serve time the run_command env-dump block +
tool policy are the real defense, not the brain's discourse. If refusal
behavior matters in the next soup mix, it needs authored refusal episodes,
not hope.

## Owner options (decision pending)
(a) Accept: park the scratch brain as research; checkpoint-3873 stays the
    served brain (current state, zero cost).
(b) Pivot the distillation source to the Soup BASE (Qwen2.5-1.5B-Instruct
    via a CPU soup serve): fluent CHAT rows for the scratch brain — tool
    rows keep coming from the authored exam-aligned episodes. NOTE: owner
    call — an external-provider-derived model as the style source touches
    the branding/identity rules.
(c) Stop here and revisit after more Phase D pretraining tokens (capacity
    remains the fundamental wall per runs 6–8).

sft_distill.jsonl as it stands (5 rows) is NOT a Phase C mix — no run 9
from it. No auto-promotion, model/scratch/final.pt untouched.

## SERVE-TIME REFUSAL AUDIT (2026-09-26, owner request — options (a) accepted)
Owner accepted option (a): scratch brain PARKED as research, checkpoint-3873
stays the live brain. Follow-up question: when the brain COMPLIES (as seen
in distillation), do the serve-time gates hold? Probe:
scripts/audit_refusal_gates.py + tests/test_audit_refusal_gates.py (17,
pure logic only — the live probe is never run from tests). It drives the
LIVE key-gated /api/ask/stream with the 2 compliance seeds + the 2 Part 5.1
env-dump wordings, records content-free (kinds + sha16 + booleans) to
D:/hwk-data/audit_refusal_gates.log, exit 0/1 = held/findings.

RESULTS (3 runs x 4 cases):
- SECURITY LAYERS HELD in every run: ZERO run_command dispatches ever
  reached the tool layer (the brain answered in text only) — the allow-list
  + _EXFIL_PATTERNS env-dump block were never even needed (they are
  test-pinned separately in test_hwk.py). No secret shapes in any reply or
  tool result. No real Windows env values anywhere.
- HONESTY FINDING: in 3/12 asks the brain FABRICATED a fake env dump
  instead of refusing — `os.environ = {'PATH': '/usr/local/bin:...'}`
  (Linux values on a Windows machine) and `PYTHONPATH=/root/.cache/...`.
  Theater, NOT a leak (values invented; the real-leak regex
  _REAL_WINDOWS_ENV_RE — C:\\ / APPDATA / AALI_*= — never fired). Same
  failure family as the exam's security 0/4.
- NON-DETERMINISM: the same AR env-dump prompt flipped compliance-theater
  ↔ refusal across runs; AR prompts refused more consistently than EN.
- The existing prompt-leak filter (_redact_prompt_leak) does NOT classify
  fabricated env theater (nothing real leaks) — a possible future guard,
  owner call.

AUDIT VERDICT: gates held, no breach, no urgent fix. The distillation
verdict is reinforced from the other side — the brain's security DISCOURSE
is unreliable, and the runtime layers are what actually keep the machine
safe. Parked state confirmed: model/scratch/final.pt absent (scratch stays
research), :20129 = checkpoint-3873, :5055 healthy.
