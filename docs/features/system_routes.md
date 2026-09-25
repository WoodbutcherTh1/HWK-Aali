# System routes + owner preview (Track B 11.6 / 11.7 — 2026-09-25)

## 11.6 — Role-gated system routes

Four new surfaces on the API server, gated by the Track B roles table
(`file_agent/roles.py`) through the SAME `_require_role()` contract used
everywhere: **404, never 403** — a caller without the permission cannot
even tell the route exists.

| Route | Permission | Who |
|---|---|---|
| `GET /api/system/training` | `training` | owner |
| `GET /api/system/models` | `models` | owner + dev |
| `GET /api/system/logs?file=&tail=` | `logs` | owner + dev |
| `GET /api/system/health_full` | `health_full` | owner + dev |
| `GET /api/auth/whoami_raw` | `advanced_settings` | owner + dev |

- `/api/system/training` returns the `aali_jobs.snapshot()` board
  (GPU, Phase D, caretaker, pipeline, Pi-CI, disks) — the same read-only
  snapshot the CLI `/jobs` and the desktop pill use.
- `/api/system/models` reports the served brain (`_brain_summary()`),
  the Ollama interim model, own-model flag, and `D:/hwk-models`
  directory metadata (names + sizes; weights are never read or served).
- `/api/system/logs` tails a WHITELIST of logs (`_LOG_FILES` in app.py:
  server, phase D/C, tts, tool-guard, gpu-gate, tunnel), tail-capped at
  1000 lines, and every line passes `redaction.redact_secrets()` before
  leaving the machine. Unknown names → 400 with the whitelist listed.
- `/api/system/health_full` = the public `/api/health` body PLUS
  uptime, in-memory session count, search-index size, TTS availability
  and live job alerts — the numbers a watchdog must never see.
- `/api/auth/whoami_raw` shows the RAW role verdict next to the
  effective one — makes 11.7 preview visible while testing.

## 11.7 — Owner preview mode ("view as user")

The owner can downgrade THEIR OWN session to the `user` role to check
exactly what a normal user sees — without logging out or using a second
browser.

- `POST /api/auth/preview` → start; `DELETE /api/auth/preview` → stop.
  Both 404 for user/guest callers (the surface does not exist for them).
- Two carrier paths: an **account session** gets a server-side flag
  (`accounts.set_session_flag`, stored in the session record — a client
  header/payload can NEVER set or clear it); a **master-key/local
  owner** (no session token) gets a bounded process-global timer
  (default 30 min, `{"ttl": seconds}` to customize, dies with the
  process).
- While previewing, `_resolve_role()` returns role `user` with
  `previewing: true` — never lower, never higher. Every gated surface
  (admin routes, system routes, `/api/auth/me`'s `aali` block) responds
  exactly as it would for a real user.
- The web UI shows a gold **role badge** in the chat header
  (owner/dev/admin only — hidden for user/guest, HIDE not disable), a
  «👁 معاينة» toggle, and a fixed gold banner with an exit button while
  previewing. Role state re-fetches from `/api/auth/me` after every
  toggle; the server re-checks every request regardless.
- Preview start/stop is audited (`preview_start` / `preview_stop`,
  content-free) to `file_agent/audit_log.jsonl`.

## Tests

`tests/test_roles_routes.py` (13): owner-only 404 contract on all four
system routes, dev access via `AALI_DEV_EMAILS`, board shape with a
stubbed `aali_jobs`, log whitelist + redaction + traversal rejection,
health_full superset, whoami_raw gating, preview module flag lifecycle,
404 control-route gating, server-side downgrade visible in
`/api/auth/me`, client-cannot-clear, master-key timer window, and the
never-elevates invariant.
