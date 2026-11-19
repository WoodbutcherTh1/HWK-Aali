# آلي — Live UI

React + TypeScript + Vite client for the Aali engine.

## Run

```bash
cd aali-ui
npm install
npm run dev
```

- Local: <http://localhost:5173>
- Phone on the same network: `http://<PC-LAN-IP>:5173`

Vite proxies `/api` to `http://localhost:5000` by default. Override the engine
target without changing code:

```bash
VITE_API_TARGET=http://localhost:5055 npm run dev
```

For a non-proxy deployment, build with `VITE_API_BASE=http://host:port`. If the
engine runs in key mode, the browser needs an admin key for the global live feed
and proposal approval; the UI reads it from `VITE_API_KEY` at build time or the
`aali_ui_api_key` localStorage key.

## What is wired

- `POST /api/ask/stream` with live `activity`, `scratchpad`, proposal, surface,
  and `done` events.
- `GET /api/brain/stream` as the cross-device live workspace feed (local mode is
  open; key mode requires admin). When unavailable, the sidebar falls back to
  the current turn's events.
- `GET /api/proposals?status=pending`, `GET /api/proposals/<id>`, and
  `POST /api/approve` for the review modal.
- `SURFACE_SWITCH` changes the banner/mode accent.
- Desktop is a two-pane chat + workspace layout; mobile uses Chat / Workspace
  tabs.

## Security note

The dev server binds `0.0.0.0` so a phone can reach it. That also exposes the
Vite proxy to your LAN. Run it only on a trusted network; for key-mode engines,
use an API key rather than exposing an unauthenticated local engine.
