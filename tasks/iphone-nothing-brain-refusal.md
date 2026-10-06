# iPhone "he did nothing" — DIAGNOSED + FIXED (deterministic layer)

owner: pc/agent
status: done
started: 2026-10-06
finished: 2026-10-06

## Resolution pointer
- Fix-plan item 2+3 are LIVE: `agent_loop._machine_facts_fast` answers
disk/RAM/GPU from the OS deterministically (tests/test_machine_facts.py, 10
green; the model is never consulted for a machine fact).
- Fix-plan item 1 (bigger brain) was executed and MEASURED the same evening:
see tasks/level-up-brain-past-3933.md — the raw 3B LOST the A/B 3/6 to the
tuned 1.5B (fabrication + Arabic mixing), so 3933 stays served and the 3B
tuning is armed behind the disk gate.

## What the owner reported
"i tried to let him do things on my iPhone as a user he did nothing"

## Live diagnosis (measured, not guessed)
The phone is NOT the problem. Everything on the path works:

| Check | Result |
|---|---|
| brain `:5055/api/health` | 200 `{"mode":"local","ok":true}` |
| `POST /api/ask` with NO key | **404** (key mode — the uniform gate) |
| `POST /api/ask` with master key, loopback | **200** + real reply |
| same key + `X-Forwarded-For` (what the tunnel/phone looks like) | **200** + real reply |
| master key `is_admin` | true → the remote-guest block never fires |
| stream of a tool-requiring ask | `provider_selected aali_own`, **NO `tool_requested` event** |

So auth, key mode, the tunnel path and the guest policy are all fine. The
failure is the LAST hop: **the brain does not call a tool, and it does not
admit ignorance.**

## The real defect
`How much free disk space is on the C drive?` (master key, loopback, streaming):

- run 1 → `I'm sorry, I am unable to find any free disk space on the C drive.`
- run 2 → scratchpad: *"approximately 1.2 GB"* → final: *"approximately 1.2 GB"*

Ground truth on the same box: **385 GB free**. The 1.5B invented a number
instead of calling `machine_ops`, and flips between refuse / fabricate run to
run. This is the `checkpoint-3933` ceiling already recorded in AGENTS.md
(3/39 on the exam, hallucination + refusal instability).

**iOS client is not the blocker** — `ios/Aali/APIClient.swift` already carries
`X-API-Key` (added 2026-10-03). SwiftUI cannot be built on Windows, so the app
path cannot be verified here; the web client over LAN/tunnel is the honest
verification route and it reaches the brain correctly.

## Fix plan (owner delegated the decision)
1. Serve a genuinely bigger model and A/B it on these exact failing asks.
2. Deterministic server-side answers for machine questions (disk/RAM/GPU/power)
   so the numbers are REAL regardless of how the model behaves.
3. Never let a machine-fact question be answered from the model's memory.