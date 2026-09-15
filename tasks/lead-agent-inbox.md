# Lead Agent inbox (DeepSeek/Gemma) — coordination channel

Owner appointed a Lead Agent (DeepSeek/Gemma) for high-level architecture +
UI/UX direction. Buffy (Freebuff coding agent) works through the shared repo;
this file is the message board. Leave notes here, claim areas via your own
`tasks/<name>.md`, never edit another agent's claim file.

## From the OWNER (via Buffy) — 2026-09-15, day session — the open questions are ANSWERED

The owner reviewed the four open UI questions and the owner-items list and
decided by best practice; Buffy implemented the implications same-session
(suites 675 green .venv / 130 green hub subset, commit below):

- **Q3 menu bar: REJECTED.** In-page buttons stay. A native menu bar is an
  extra platform-specific layer for zero information gain in a 2-action UI;
  the tray already carries show/quit.
- **Q4 first-run token entry: BUILT** — `aali_node/node_config.py` + daemon
  `--config` / `--save-config`. Owner-only JSON at
  `%APPDATA%/AaliNode/aali-node.json` (POSIX: `~/.aali-node/`, chmod 600),
  precedence flag > env > saved config > builtin; `token_file` indirection
  for rotation; secrets redacted in every view. The shell stays a viewer;
  once the daemon boots from saved config no token touches the UI.
- **Q5 staged-update banner: BUILT** — the daemon publishes a structured
  content-free `update_staged` field (VERSION only) and the shell renders a
dismissible gold banner ("تحديث مُوقَّع جاهز للتفعيل — الإصدار …").
- **Q6 windowed exe: REJECTED for now.** The headless CLI is the daemon's
  primary contract; a windowed exe would hide CLI output. Revisit only with
  a real GUI-first distribution story.
- **Q7 code-signing: OPEN, owner-only** (EV cert purchase). Everything else
  is signed-verify-by-default; SmartScreen warnings are the known cost.
- **Q8 VPS + secrets: secrets DONE** — `scripts/aali_hub_secrets.py`
  generated the real production set into `D:/hwk-data/aali-hub-secrets/`
  (JWT secret, brain token, protocol master, **Ed25519 seed + public hex**,
  ready-to-source `hub.env`; owner-only perms, never overwrites, never in
  the repo). The production Ed25519 path is now REAL: env keys that parse as
  seed hex load as Ed25519 (`load_signing_key`), HMAC stays the fallback —
  before this session an env key could only ever be HMAC. Owner still owes
  the VPS itself; deploy = copy `hub.env` → `/etc/aali-hub/env`, chmod 600,
  source before uvicorn.
- **Q9 HuggingFace/OpenRouter: DECIDED — defer.** Not needed until layer-0
  external distribution goes live; revisit after the Hub has real users.
- **Q10 Wake-on-LAN: DIRECTIVE** — the Hub must stay honest: without
  `AALI_WOL_MAC` configured, WoL advertises itself as unavailable instead of
  pretending to wake the brain. (The wol module already behaves this way;
  now it is policy, not accident.) Owner runs the NIC/BIOS check when the
  VPS exists.

— recorded by Buffy on the owner's behalf

## From Buffy — 2026-09-15, night watch

- Baseline: suite 521 passed / 1 skipped in the training venv. Tree clean at
  7d5d83b (main). Disk D: 22% free, X: 36% — both PASS the 20% rule.
- Tonight I claimed (see tasks/saas-transformation.md): **STEP 4**
  (AALI_MODE=saas wiring in file-agent/app.py: orchestrator + per-user memory
  namespaces via the existing contextvar scope + /api/brain/status) and the
  **STEP 5 sandbox core** (aali_node/sandbox.py + headless daemon — the
  security-critical piece, no UI shell yet).
- If you take UI/UX direction: the web client lives in `web/src` (Arabic-first
  RTL, dark/sepia themes, dist served at /ui/). Desktop polish layer landed
  2026-09-12 (see AGENTS.md §5). Please write UI direction as notes here or in
  your own tasks/ file rather than editing web/src directly while I hold the
  SaaS claim — I'll fold direction changes into the build.
- Constraints I'm holding (AGENTS.md, non-negotiable): local commits only,
  NO git push while the owner sleeps, no training/GPU launches, ports stay
  5055 (brain) / 5678 (n8n), never kill processes by guess, no new deps in
  the training venv (hub deps live in .venv-hub only).
- Live brain untouched: promoted checkpoint-3873 on :20129, API on :5055.
- When you finish a work item, leave a short note in this file under a
  "From Lead Agent" heading with the commit hash; I'll rebase my remaining
  work on top rather than parallel-editing the same files.

## From Buffy — 2026-09-15, night watch, status at ~03:30 local

DONE tonight (commits below, suite 562 green):
- **STEP 5 core shipped**: `aali_node/` — `sandbox.py` (SecureSandbox:
  workspace jail w/ UNC/ADS/drive/NUL refusal, per-tool path-arg table,
  confirmation reconciliation either-side-wins, timeout cap 120s,
  secret-redacted output) + `daemon.py` (NodeSession + headless WS daemon,
  `python -m aali_node --hub ... --token <JWT>`, stop-event for embedders).
- **LIVE e2e proven**: tests/test_aali_node_live.py — real uvicorn Hub +
  real daemon over real WebSockets; brain dispatch → node sandbox → relayed
  signed result. Runs in .venv-hub, auto-skips in .venv.
- Refactor: secret redaction extracted to `file_agent/redaction.py`
  (stdlib-only; memory.py re-exports) so the Node doesn't need `requests`.

Lessons for whoever touches this next:
- file_tools' `requests`/`memory` import chain is brain-only — node-side
  code must import `file_agent.file_tools` lazily and use
  `file_agent.redaction`, never `file_agent.memory`.
- Never run the daemon via asyncio.to_thread inside a test: the thread
  outlives the loop and pytest hangs forever (use a daemonized thread +
  stop Event).

Next up (mine, unless you claim it): pywebview shell + native confirm
dialogs + tray; then Hub `update_server` wiring into the Node.

## From Buffy — shift close (~05:00 local)

Also landed since the status above:
- confirm.py shipped anyway (native ctypes dialog, 60s auto-deny, console
  fallback) — commit d479b72. The tray/window shell is still yours to
  direct.
- Hub ask loop: broker routes user_request/final_reply with JWT-verified
  identity override (anti-spoof) — commit bc72d1b. So the FULL loop now
  exists end to end: ask → brain → tool dispatch → node sandbox → result →
  final reply.
- Production-log debt from the previous night report closed (19035b5).
- Final: 585 green (.venv), 171 green (.venv-hub subset). 5 local commits,
  nothing pushed — the owner reconciles us before pushing.

If you worked overnight, add your "From Lead Agent" section here and push
nothing; I'll rebase on your hashes when the owner says go.

## From Buffy — 2026-09-15, same night, update channel

- Hub update_server now has its HTTP surface + the Node has a verified
  update client. Publish→serve→retire proven by tests on both sides
  (603 green .venv, 100 green hub subset). Routes only exist when
  AALI_UPDATE_SIGNING_KEY is set; Ed25519 auto-selected with
  `cryptography` (installed into .venv-hub — was already pinned).
- If you take the pywebview shell: the daemon exposes an opt-in
  non-fatal update check (--update-hub + --update-key); the shell can
  call run_node(...) with update_hub=/update_key= kwargs, or surface
  staged updates under <install>/staged/<version>/ itself. Activation
  (swap + restart) is deliberately NOT built — say the word if you want
  it in the shell's domain, otherwise it stays backend.
- No push, as always — owner reconciles.

## From Buffy — 2026-09-15, later that night — pywebview shell + tray SHIPPED

- **The Node GUI exists**: `aali_node/shell.py` + `python -m aali_node
  --shell`. Daemon thread (`native_confirm=True` — the shell IS GUI mode) +
  pywebview window + pystray tray. Arabic-first RTL, warm-black #252523 +
  gold — the product language; no direction was in the inbox at build time,
  so I kept it deliberately thin: it renders the daemon's own status
  (connection state, counters, workspace, commands on/off, capped event
  log) and invents nothing. If you want a different shape, ONLY
  ShellState/_PAGE need to change — the daemon contract is untouched.
- Behavior you should know about: close button = hide-to-tray while the
  daemon is alive (a Node dying because a window closed would strand the
  brain mid-task); real exit is the tray menu or the إنهاء button; without
  pystray/Pillow the shell still runs (close = quit, honest notice); with
  no GUI backend it falls back to headless serving rather than dying.
- The daemon grew an optional CONTENT-FREE status dict (state, hub_url,
  node_id, counters, last_event — never tool names/paths/content) that any
  embedder can poll; headless `run_node()` is unchanged otherwise.
- Deps: pystray + Pillow pinned in requirements-hub.txt, installed into
  .venv-hub only. Suites: 615 green (.venv), 157 hub subset incl. live e2e.
- Open UI/UX questions for you (answer here, I'll fold in): (1) native
  menu bar vs the in-page buttons, (2) first-run token entry screen (now:
  token comes from CLI/env), (3) whether staged-update notification
  deserves a visible banner once activation is built.

## From Buffy — 2026-09-15, day session — the Node is now an EXE

- **Packaging shipped** (the item I said was "the next STEP 5 item" at
  shift close): aali-node.spec + launch_aali_node.py shim +
  scripts/build_node.bat → build-desktop/dist/aali-node.exe. The exe IS
  the headless daemon (same argv contract as `python -m aali_node`, --shell
  still opens window+tray). First build died at launch with a green build
  log — sandbox imports file_agent.protocol at module level and the
  PyInstaller analysis pass needs file-agent/ on pathex; fixed and pinned
  by tests/test_aali_node_packaging.py (8). PROOF: smoke_aali_node.py
  --frozen-exe ran the packaged exe against a real Hub — brain dispatch →
  exe sandbox → signed result wrote a real file, 7/7 phases. cryptography
  ships inside the exe (production Ed25519 update path).
- For you: the UI questions from the shell note above are still open.
  New one: (4) the exe is console=True on purpose (headless CLI is the
  daemon's primary contract; --shell still pops the native window) — if
  you want a windowed exe instead, that's a one-line spec change plus a
  decision on how CLI users get output.
- Frozen --activate-update is refused by design (source-deploy path); if
  you want packaged updates to swap+restart, that needs a PyInstaller
  onedir layout or an installer-owned activation — say the word.

— Buffy
