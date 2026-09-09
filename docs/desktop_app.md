# Aali as a product — one server, every client

*Built 2026-09-07 at the owner's direction: "Aali will be on servers and users
talk to him via web, desktop app, CLI, terminal."*

## The architecture

```
                ┌────────────────────────────┐
   web/PWA ────▶│                            │
   desktop ────▶│   Aali server (Flask)      │──▶ brains: Aali's own trained
   CLI ────────▶│   /api/* + SSE streaming   │    model (soup) / local chat
   terminal ───▶│   per-user sessions        │    brain / direct-command mode
                └────────────────────────────┘
```

One server, four client types, one API. Every client gets the same features:
streaming with live tool activity, suggestion chips, session history.

## The API

| Endpoint | What |
|---|---|
| `POST /api/ask` | Classic request/reply (CLI, external tools, n8n) |
| `POST /api/ask/stream` | **SSE**: live `activity` events (tool_requested/tool_result/provider) then one `done` event with reply + suggestions |
| `GET /api/sessions` | Sidebar rows (sid, title, turns, updated_at) |
| `GET/DELETE /api/session/<sid>` | Load / delete one conversation |
| `POST /api/compact` | Fold old turns into a summary |
| `GET /api/health` | Liveness probe |
| `POST /v1/chat/completions` | a major AI vendor-compatible (the external IDE mentor/Aider/Zed can use Aali as a model) |

### Multi-user mode

Set `AALI_API_KEY` on the server → every `/api/*` call must carry the same
value as `X-API-Key` (or `?key=`), and **each key gets isolated sessions** —
two users on one server never see each other's conversations. Unset = local
single-user mode, unchanged behaviour.

## The clients

- **Web / desktop app** — `web/` (React + Vite). `npm run build` → `web/dist/`,
  served by the server at `/ui/`. Installable as a desktop/mobile PWA
  (manifest + service worker included). Features: streaming + live activity
  ticker, markdown with copy-able code blocks, suggestion chips, sessions
  sidebar (open/delete), voice input, stop button, slash commands, policy
  selector, GitHub review panel, compact.
- **CLI / terminal** — `aali_cli.py` (one-shot or REPL, suggestion chips,
  `--server`, honours `AALI_API_KEY`).
- **Anything else** — the a major AI vendor-compatible endpoint or plain `/api/ask`.

## Run it

Local (this machine):
```
.venv/Scripts/python file-agent/app.py        # server on :5055
python aali_cli.py "ما الأخبار؟"              # terminal client
# web app: open http://127.0.0.1:5055/ui/
```

Server / VPS:
```
docker compose up -d --build                  # production (waitress WSGI)
AALI_API_KEY=choose-a-key docker compose up -d  # multi-user mode
```

Rebuild the web client after UI changes: `cd web && npm run build`.

## UI development / testing without the GPU

`scripts/mock_aali_api.py` serves canned API responses (same shapes, including
SSE) plus the built app at `/ui/` on port 5099 — the full UI can be exercised
while training occupies the GPU.
