# aali-ui — React/TypeScript live frontend

owner: Buffy
status: done
started: 2026-11-06
finished: 2026-11-06

## Scope

Build and run the Vite React/TypeScript client in `aali-ui/`:

- dark chat UI streaming from `POST /api/ask/stream`
- live workspace sidebar fed by `GET /api/brain/stream`, with per-turn
  `activity` fallback
- `SURFACE_SWITCH` banner and mode accents
- `proposal_created` review modal with unified diff, approve, and reject
- desktop chat + workspace split; mobile Chat / Workspace tabs
- Vite proxy from 5173 to the engine assumed at `http://localhost:5000`

## Verification

- `npm install`: 69 packages, 0 vulnerabilities.
- `npm run build` (`tsc --noEmit && vite build`): passed.
- Vite dev server listening on `0.0.0.0:5173` (PID 1860).
- `http://127.0.0.1:5173/` → HTTP 200.
- `http://192.168.1.12:5173/` → HTTP 200.

At verification time no process was listening on port 5000, so the UI shows
the engine offline state and the proxy health request fails until the engine is
started. No backend process was started by this task.

## Known limits

- The global feed is open in local mode; in key mode it needs an admin key.
- Proposal approval follows the engine's loopback/admin rule.
- The dev server is LAN-exposed by design for phone access; use a trusted
  network.
