owner: Buffy (Freebuff agent session)
status: in-progress
started: 2026-09-14

## Mission
Transform Aali into "Aali Cloud" — a multi-tenant SaaS code agent with
external distribution. Four layers: (0) external distribution
(OpenRouter / OpenCode / HuggingFace / vLLM / any OpenAI-compatible client),
(1) Aali Hub (auth, WebSocket gateway, queue, registry, updates, audit),
(2) Aali Brain (owner's Windows PC; SaaS mode in file-agent/app.py),
(3) Aali Node (lightweight desktop app executing tools on the user's machine).
Full mission brief was supplied by the owner in-session.

## Session scope (this claim)
- Deliverable 1: docs/SAAS_ARCHITECTURE.md (bilingual EN+AR, honest status).
- STEP 1: `TOOL_EXECUTION` + `CONFIRM_REQUIRED` in file_tools.py,
  `file_agent/tool_orchestrator.py`, `tests/test_tool_orchestrator.py`.
- STEP 2: `file_agent/protocol.py` (versioned signed JSON messages),
  `tests/test_protocol.py`.
- Commits stay small; nothing is pushed.

## Not started (needs owner decisions or later steps)
- STEP 3 Hub: new FastAPI service + new dependencies -> needs a separate
  venv and explicit owner go-ahead (no new heavy deps in the training venv).
- STEP 5 Node: Tauri vs Electron decision; Rust toolchain availability.
- Code-signing certificates (EV/OV/Apple), Hub VPS availability,
  Wake-on-LAN-capable NIC/BIOS, HuggingFace/OpenRouter accounts.

## Coordination notes
- No overlap with phase-c-own-brain-sft-relaunch (run 6 finished, archived)
  or v5-graduation-run (done).
- Live brain untouched: no changes to model/scratch/final.pt, promoted
  checkpoints, or soup.yaml. Local mode (AALI_MODE=local) stays the default
  and unchanged.
