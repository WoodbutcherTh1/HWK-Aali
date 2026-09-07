# Run doc — Aali desktop app (server + web UI)

The "app" is one Flask server (file-agent/app.py) serving both the API and the
built web client at /ui/. No npm dev server needed for viewing — web/dist is
prebuilt and committed.

## Reproduce artifacts (fresh checkout)
- No env files are required for local mode. For multi-user mode set
  `AALI_API_KEY` (any long random string) when launching.
- Web client is prebuilt in `web/dist/` (committed). To rebuild after UI
  changes: `cd web && npm install && npm run build` (Node 20+; dist output is
  served by the server, no separate frontend process).
- Ollama (optional, recommended): install Ollama and `ollama pull llama3.1:8b`
  — the agent loop uses it automatically as the conversational brain when the
  Ollama server (port 11434) is running.
- Scratch-model brain (optional): set `LOCAL_MODEL_PATH` to a .pt checkpoint
  (e.g. D:/hwk-models/scratch/final.pt). Without a brain, /api/ask still works
  in deterministic direct-command mode.

## Run the server (Windows, detached)
From the repo root:
```
powershell -NoProfile -Command "$env:PORT='5055'; Start-Process -WindowStyle Hidden -FilePath '.venv\Scripts\python.exe' -ArgumentList 'file-agent\app.py' -RedirectStandardOutput 'D:\hwk-data\aali_server.log' -RedirectStandardError 'D:\hwk-data\aali_server.err'"
```
- `.venv` = project venv with Flask + requirements from file-agent/requirements.txt
- Optional env: `AALI_API_KEY` (require X-API-Key on /api/* + per-user
  sessions), `LOCAL_MODEL_PATH`, `CUDA_VISIBLE_DEVICES=""` (force CPU brain
  while the GPU trains), `AGENT_WORKSPACE` (tool sandbox root).
- Health check: `curl http://127.0.0.1:5055/api/health` (with the key header
  when AALI_API_KEY is set).

Then register the preview with the server's PID (netstat -ano | findstr 5055).
