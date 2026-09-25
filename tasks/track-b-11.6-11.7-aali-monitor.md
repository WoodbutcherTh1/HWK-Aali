# Task claim: Track B 11.6–11.7 + Aali Monitor

- owner: Buffy (Freebuff) on the PC
- status: done
- started: 2026-09-25

## Scope (owner GO, 2026-09-25 ~14:30)

1. **Track B 11.6 — role-gated routes**: `/api/system/training` (owner),
   `/api/system/models`, `/api/system/logs`, `/api/system/health_full`
   (owner+dev). Uniform-404 contract via `_require_role()`. Role badge in
   the web UI (hidden for user/guest — HIDE, don't disable).
2. **Track B 11.7 — owner preview mode**: server-enforced "view as user"
   (cookie flag on the owner's session, client cannot set/clear it),
   `/api/auth/preview` POST start / DELETE stop, gold banner + exit in the
   web UI. Never treats a remote caller as owner; role computation stays
   server-side only.
3. **Aali Monitor (side project, owner-ordered)**: desktop companion —
   pywebview + pystray over a headless daemon, log parsers reading
   D:/hwk-data logs (aali_server.log, phase_d_*.log, soup pipeline,
   status_digest STATUS.md), tray notifications, build-desktop/aali-monitor/
   with .venv-monitor. Deliverables: tests/test_monitor_parsers.py +
   docs/monitor.md.

## Rules honored

- 404 not 403 on every gated surface; HIDE not disable in UI.
- No new deps in the training venv (.venv); monitor deps stay in
  .venv-monitor.
- Tests + docs before commit; no push (owner reviews).

## Status log

- 2026-09-25: claim created; roles.py + _resolve_role/_require_role read;
  design settled (preview flag = server-side session attribute).
- 2026-09-25 15:45: DONE. 11.6 four system routes + whoami_raw live-verified
  (owner 200 / anon-user-admin 404); 11.7 preview live-verified end-to-end
  (start -> /api/auth/me shows role user + previewing:true -> gated surfaces
  404 -> stop -> owner restored; master-key ttl window). Monitor shipped:
  core parsers + shell + --headless/--once, monitor-venv created at
  D:/hwk-tools/monitor-venv, --once proven against live logs (PROMOTE
  verdict + GPU + services), Desktop shortcut created. Suite 987/9 green;
  web rebuilt (tsc+vite clean). Docs: docs/features/system_routes.md,
  docs/monitor.md. Lesson logged in AGENTS.md: missing docstring opener
  pointed the SyntaxError at the wrong line — bisect with compile(prefix).
- status: done
