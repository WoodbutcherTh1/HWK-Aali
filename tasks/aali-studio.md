# PART 2 — Aali Studio (desktop IDE)

owner: buffy (this PC, Freebuff session)
machine: PC (heavy) — CPU-only work, no GPU jobs
status: done (PART 2)
started: 2026-10-02

## What the owner asked for

A Cursor/Freebuff-style **desktop IDE** where Aali is the default model.

- Name: **Aali Studio**. Default model label: **HWK-AZiZA** (local brain on
  :5055). Tribute to the owner's grandmother (Azeza / عزيزة) — the About
  dialog MUST mention her.
- UI: 3 panes — file tree | Monaco editor + terminal | AGENT PANEL.
- Agent panel (CRITICAL): 💭 THINKING (gray/italic/collapsible),
  🔧 TOOL CALL (name + args + result), ▶️ TERMINAL streaming,
  ✏️ FILE EDIT DIFF (before/after), ✅ FINAL ANSWER,
  ⏹️ STOP while working, ▶️ RETRY after failure.
- Model switcher always visible: HWK-AZiZA (default, local), GLM-4-Flash,
  GLM-4-Plus, DeepSeek-V3, DeepSeek-R1, Groq (Llama 3.3), OpenRouter (free),
  Together AI (free), Custom endpoint. **API only, no local downloads.**
  Keys stored under `%APPDATA%/AaliStudio/`.
- Tech: Python + Flask backend on **:5070**, pywebview window, Monaco via
  CDN, SSE streaming. Isolated venv `.venv-studio`, CPU-only, sandboxed file
  ops, Arabic-first labels.
- Layout: `build-desktop/aali-studio/{studio_app.py, studio_server.py,
  models_proxy.py, web/{index.html,style.css,app.js}, build.bat}` →
  `Aali-Studio.exe`.
- Endpoints: `GET /api/tree`, `GET /api/file`, `POST /api/file`,
  `POST /api/delete`, `POST /api/run`, `POST /api/chat` (SSE),
  `GET /api/models`, `POST /api/keys`, `GET /api/keys/status`.
- SSE events: `thinking{token}`, `tool_call{name,args}`,
  `tool_result{ok,summary}`, `terminal{line}`, `diff{path,before,after}`,
  `text{token}`, `done`.

## Design decisions taken (recorded so the next agent does not re-litigate)

1. **The brain is proxied over HTTP, not imported.** Studio's
   `POST /api/chat` calls `:5055 /api/ask/stream` (`verbose: true`) and
   TRANSLATES the brain's SSE vocabulary into the Studio panel events. Reason:
   `.venv-studio` then needs only flask + pywebview (no torch, CPU-only, tiny
   exe) and there is exactly one brain on this machine — no second copy of the
   model, no duplicated policy logic.
2. **The sandbox is reused, not reimplemented.** `studio_server` imports
   `file_tools` from `file-agent/` (stdlib-only module) and routes every file
   op through the pentested `_resolve()` — traversal / device names /
   %-payloads / drive letters stay refused for free.
3. **`text{token}` is real only for streaming providers.** The local brain
   answers in ONE message (its `done` event carries the full reply), so the
   Studio replays that verbatim reply word-by-word with `AALI_STUDIO_REPLAY_MS`
   (default 10ms) pacing. The content is never invented; only the pacing is
   cosmetic, and this is documented in docs + the About box. Providers that
   really stream (`stream: true`) — including DeepSeek-R1's `reasoning_content`,
   which is mapped onto the 💭 THINKING block — deliver genuine tokens.
4. **Diffs are computed client-side of the brain, not by it.** The brain's
   `tool_requested` arrives BEFORE the write executes, so the Studio snapshots
   the target file at that moment and emits `diff{before,after}` when the
   matching `tool_result` lands. No brain change was needed.
5. **⏹️ STOP is honest.** `:5055` has no server-side cancel endpoint, so STOP
   closes the upstream connection and freezes the panel; a local turn already
   in flight may still finish on the brain. Documented, not hidden.
6. **:5070 binds 127.0.0.1 only** and rejects non-loopback `Host` headers
   (DNS-rebinding defense) — it is a filesystem-capable IDE endpoint.

## Log

- 2026-10-02 — claim created, design recorded, build started.
- 2026-10-02 — BUILT: `studio_server.py` (Flask :5070, sandboxed via
  file_tools, SSE), `models_proxy.py` (9 models, keys in %APPDATA%),
  `studio_app.py` (pywebview), `web/` (3 panes RTL + Monaco + fallback),
  `build.bat` + `Aali-Studio.spec`, `scripts/aali_studio.bat`,
  `docs/features/aali_studio.md`, `tests/test_aali_studio.py` (53).
- 2026-10-02 — `.venv-studio` created (flask + pywebview + pyinstaller);
  exe built: `build-desktop/dist/Aali-Studio.exe` 13.3 MB, WebView2 + web/
  verified inside the archive, and the FROZEN exe proven live on :5070
  (health, UI, tree, sandbox refusal, keys status, real SSE chat).
- DEFECTS found by running it (all test-pinned):
  1. **`http.client` buffered reads killed the diffs.** `_iter_sse` used
     `stream.read(1024)`, which BLOCKS until 1024 bytes — a whole turn was
     buffered and every file was snapshotted AFTER the agent rewrote it, so
     no `diff` event could ever fire. Now iterates LINES (the AGENTS.md
     lesson "a build can finish green and still die at launch" applies to
     streams too).
  2. **The brain runs in KEY MODE** — every gated :5055 route answers a
     polite 404 without X-API-Key (only /api/health is exempt). Studio now
     carries a brain key (same key store, field `brain`), sends it as
     `X-API-Key`, and says so explicitly instead of failing silently.
  3. **A frozen-path leak**: `CFG_DIR` was a module constant read at import,
     so tests that changed `AALI_STUDIO_CONFIG_DIR` still wrote to the REAL
     %APPDATA% (a fake brain URL and `model: deepseek-r1` leaked into the
     owner's settings.json). Now `cfg_dir()`/`keys_file()`/`settings_file()`
     resolve fresh every call.
  4. **PyInstaller doubles the spec-relative path** (`build-desktop/aali-studio/
     build-desktop/...`) — every path in the spec now resolves from the repo
     root via `SPECPATH`.
  5. `run_command(timeout=...)` — the brain's kwarg is `timeout_seconds`.
  6. **A sleep-based test went flaky under full-suite load** (the diff test
     passed alone, failed once in the full run): the fake brain slept 50 ms
     before writing, which is not enough on a busy box. Replaced with a
     handshake — the fake writes only after Studio has REALLY snapshotted
     (a watched `_safe_read` sets an Event), so the ordering the real brain
     guarantees is now deterministic. 3 consecutive reruns green + the full
     suite green.
- HONEST LIMITS (in docs + the About box): `text{token}` is genuinely
  streamed only by API providers — the local brain answers in one message
  and Studio reveals it word by word (verbatim content, cosmetic pacing,
  `AALI_STUDIO_REPLAY_MS`); STOP closes the stream and a local turn may
  still finish (the brain has no cancel endpoint); Monaco needs the CDN and
  falls back to a labelled plain editor offline.
- LIVE VERDICT: the wire path is proven against the REAL brain
  (request → provider → text → done). Live TOOL CALLS could not be shown
  because the brain itself is degraded right now — it answers every ask
  with the "direct-command mode" card and selects `scratch_model` with no
  weights. That is a brain-side condition, not a Studio one; the whole
  translation (thinking/tool_call/tool_result/terminal/diff) is verified
  against a fake brain that speaks :5055's exact vocabulary and REALLY
  performs the writes.
- STATE LEFT UP: the frozen exe running `--server-only` on :5070
  (log `D:/hwk-data/studio-exe.log`). To open the window, the owner runs
  `scripts\aali_studio.bat` (or the exe) once :5070 is free.
- FINAL: suite **1339 green / 14 skipped** (was 1285 before PART 2).

- 2026-10-02 (night) — OWNER VERDICT: «الضعيف، بسيط». He ranked five
  upgrades; ALL FIVE are built: file context (open file + selection +
  @mentions, resolved server-side), accept/reject on every diff (accept =
  VERIFY + reload, revert = guarded real write), tabs + a content-diffing file
  watcher, conversation history in %APPDATA%, markdown + Ctrl+P quick open.
  The design decision worth keeping: **accept writes nothing** — the agent's
  write already hit the disk, so «قبول» that rewrote `after` would be theatre;
  it verifies and the client reloads instead.
  DEFECTS my own new code shipped with, caught by the new tests and fixed: the
  fake-brain fixture read the request body twice (the second assert ran in the
  handler thread, killed the response and HUNG the client) — now one read via
  an `on_request` hook; the conflict check ran before the sandbox resolve, so an
  escape attempt answered «409 conflict» instead of a refusal; `write_file`
  needs `overwrite=True`, correct for an owner-confirmed revert and only there;
  and a literal NUL I typed as a markdown sentinel turned app.js «binary» to
  grep/diff — the renderer now holds fenced code aside with no sentinel at all.
  Suite 1377 green / 14 skipped (Studio file: 84).
- STATE LEFT UP: the REBUILT exe with its window open on :5070 (owner sees it).

## status: done (PART 2) — PART 3 (Aali Reach) awaits the owner's go

