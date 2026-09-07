# OmniRoute + Claude Code setup — 2026-09-06

Free AI gateway so Claude Code keeps working when the Claude subscription
quota runs out. Everything runs locally; no secrets involved.

## What was installed

| Component | Version | Where |
|---|---|---|
| Node.js LTS | v24.19.0 | `C:\Program Files\nodejs` (winget) |
| OmniRoute | 3.8.50 | npm global (`%APPDATA%\npm`) |
| Claude Code | 2.1.263 | npm global (`%APPDATA%\npm`) |

## How to use

- **`claude-free.bat`** (repo root) — starts the gateway if needed, then runs
  Claude Code through `http://127.0.0.1:20128` with `auto` model routing
  (free-tier providers: GLM, DeepSeek, GPT-OSS, Groq caps, Pollinations…).
  Double-click it, or run from a terminal. Pass any Claude Code flags:
  `claude-free.bat -p "question"` for one-shot mode.
- The normal `claude` command and the existing subscription login
  (`~/.claude/.credentials.json`) are **untouched** — both paths coexist.
- Dashboard (providers, keys, usage): <http://localhost:20128/dashboard>
- Logs: `D:\hwk-data\omniroute.log` / `omniroute.err.log`.
- Zero-config works with no API keys; add provider keys in the dashboard to
  widen the free pool.

## Maintenance notes

- The gateway is started hidden; it does not auto-start on reboot, but
  `claude-free.bat` detects that and starts it automatically.
- `omniroute configure claude` (interactive) failed with "fetch failed" on
  this machine (IPv6 localhost resolution); the direct-env launcher is the
  supported workaround here.
- npm 11+ blocks postinstall scripts by default; if reinstalling, allow
  OmniRoute's build scripts (`npm install -g omniroute --allow-scripts=...`).
- Keep HWK training in mind: the gateway uses CPU/RAM only, no GPU — safe to
  run alongside Phase A.

## Verified

- `POST /v1/chat/completions` (OpenAI format) → routed to a free provider, "OK".
- `POST /v1/messages` (Anthropic format) → routed, "OK".
- `claude-free.bat -p '...'` → full agent answer through the gateway
  ("LAUNCHER-OK-123").
