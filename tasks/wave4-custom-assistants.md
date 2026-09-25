## Live verification (scripts/verify_wave4_live.py, all passed)

1. create 201 «مدرّس الرياضيات» 🧑‍🏫 (proper Arabic end-to-end)
2. session via ask → 3. bind 200 → 4. persona LEADS the stored user
   message in sessions.jsonl («[أنت الآن تتصرف كمساعد مخصص باسم…»),
   brain replied in Arabic → 5. unbind → 6. DELETE detached the
   session → 7. store empty. Guest 404 confirmed on :5055.

# Wave 4 item 1 — Custom assistants UI

- owner: buffy
- status: done
- started: 2026-09-25
- finished: 2026-09-25 (suite 1094/10 + live-verified + committed)

Owner GO received ("Start Wave 4 custom assistants UI (owner go required)").

## Scope (this item only)

Named persona configs over the existing machinery — CRUD + session bind
+ ask-time persona injection + web/CLI clients. Persona is TEXT only;
it can never change the tool policy. Other Wave 4 items (team
workspaces, MCP client, analytics, branching, Android) NOT yet GO'd.

## Steps

1. [x] `file_agent/assistants.py` — JSONL store, caps, ns-scoped CRUD,
   persona_block (AR labeled text).
2. [x] `roles.py` — `assistants` permission (user/dev/owner; admin
   intentionally mirrors the `prompts` template).
3. [x] `app.py` — CRUD + `/active` bind endpoints, ask-time injection
   on /api/ask + /api/ask/stream (persona leads, after project
   prepend), detach-on-delete, stale-bind silent detach, guests 404.
4. [x] `tests/test_assistants.py` — 21 tests green.
5. [x] Web — 🎭 nav + AssistantsDialog + header chip + slash command;
   api.ts fns; tsc clean; dist rebuilt. (Also fixed forward: the
   ProjectsDialog "مفعّل" badge referenced an App-level variable —
   now a proper `activeId` prop.)
6. [x] CLI — `/assistants list|new|use|off|edit|del` + TAB + /help.
7. [x] Docs — docs/features/custom_assistants.md.
8. [x] Full suite (1094 passed / 10 skipped) → live restart → live
   verify (Arabic create → bind → ask → persona leads stored message
   in proper Arabic → unbind → delete-detaches → store empty) →
   commit (no push).

## Lessons hit

- `_user_ns` is request-scoped (documented): tests replicate the
  `u<key>:<sid>` format outside a request context. NOTE: with the
  master key the embedded id is the PLAINTEXT key (`_client_key()`),
  not "admin" — `_auth_state` marks master as is_admin, so `_user_ns`
  takes the `u{_client_key()}:` branch.
- Master-key bind endpoint 404s if tests plant under a guessed key —
  verified against the live `_user_ns` branch before asserting.
- Live curl from Git-Bash mangled Arabic into `?` (codepage) — NOT a
  server bug; verify with a UTF-8 Python client
  (scripts/verify_wave4_live.py kept as the one-shot checker).
- The boot can take 60-90s on this box while aili_brain_watchdog and
  old listeners linger; a health 200 from a STALE server answers curl
  while the new launch is still binding — always confirm the LISTENING
  PID changed (netstat) before trusting the health check, and never
  relaunch on a guess. Two duplicate pairs had to be killed by exact
  PID this session (both verified app.py before taskkill).
