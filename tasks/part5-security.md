# Task claim: Part 5 security track (5.1-5.3)

- owner: Buffy (Freebuff) on the PC
- status: done
- started: 2026-09-25

## Scope (owner GO, 2026-09-25 ~17:10)

1. **5.1 prompt-leak resistance probe** — 15 AR/EN injection payloads vs
   the LIVE brain (:5055 -> :20129 soup checkpoint-3873), classify replies
   (system-prompt phrases, canaries, protocol JSON, env-assignment,
   secret shapes), append per-case results to
   D:/hwk-data/security_incidents.log, exit 1 on any leak. Read-only:
   no code change, no writes outside the data dir.
2. **5.2 output filter `_redact_prompt_leak`** — 8-word shingle match
   against SYSTEM_PROMPT (+ policy prompts), tool-protocol JSON checks
   ({"tool": ...), AALI_* env-assignment lines; line-level redaction,
   honest bilingual degrade when the reply IS the leak; incident log
   (content-free: kinds + hashes, never the leaked text).
3. **5.3 canary tokens** — 3 unique integrity marks planted in
   SYSTEM_PROMPT; any canary in an output = confirmed full-prompt leak
   -> whole-reply degrade + high-severity incident.

5.4 (rate-limit review) and 5.5 (sandbox pentest) are NOT in this pass.

## Rules honored

- Probe is read-only against the live brain; incident log carries no
  reply text (hashes/marker names only) so it can never re-leak.
- Tests + docs before commit; suite stays green.

## Status log

- 2026-09-25: claim created.
- 2026-09-25 17:55: DONE. 5.1 baseline 15 cases → 1 leak (E08 protocol
  coercion; L1 spills seen once in an earlier stochastic run); 5.2 filter
  + 5.3 canaries shipped and live; re-run 15/15 clean. Suite 1043/9.
  Docs: docs/features/prompt_leak_defense.md.
- 2026-09-25 18:15: 5.4-5.5 DONE. 5.4: login/verify/reset unbounded
  guessing FIXED (5-fail lockout + code burns); existing limiters
  audited — table in docs/features/rate_limits_sandbox.md. 5.5: pentest
  suite (31 tests) — 4 real findings fixed (flag-cwd escape with a case
  bug in my own first guard, caught by the test; device names; encoded
  payloads; drive relatives); traversal/UNC/ADS/symlink confirmed solid.
  Suite 1073/10.
- status: done (5.1-5.5)
