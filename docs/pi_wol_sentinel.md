# Pi WoL Sentinel — Aali يخدم دون أن يكون الحاسوب ساهراً

**Status: LIVE (2026-09-27).** The Raspberry Pi (192.168.1.15, aarch64,
Debian 13, ~3 W) now runs the Cloudflare tunnel connector AND a
wake-on-LAN "sentinel" catcher, so the home PC (192.168.1.117) no longer
needs to stay on 24/7 just to be reachable from the internet.

## Architecture

```
Internet → Cloudflare edge → aali.dpdns.org
    ↓ (named tunnel 'aali', TWO registered connectors)
    ├─ Pi connector   : cloudflared (linux-arm64) on the Pi, ALWAYS ON
    └─ (PC connector  : retired 2026-09-27 — HWK TunnelAutoStart disabled)
    ↓ (on the Pi)
  pi_sentinel_catcher.py  (127.0.0.1:8766, stdlib only)
    ├─ proxies every request → http://192.168.1.117:5055  (streaming both ways)
    ├─ GET /api/health answered LOCALLY (409 + "waking") when the PC is asleep
    └─ unreachable PC → Wake-on-LAN magic packet (directed + 192.168.1.255:9),
       cooldown 45 s so a request burst costs at most one wake
```

PC sleep is the owner's power plan (`powercfg /change standby-timeout-ac N`);
`scripts/idle_sleep_guard.py` only CHECKS whether sleep is safe (exit 0/1) and
never puts the PC to sleep by itself.

## What was verified live (2026-09-27)

- Pi→PC LAN path: `curl http://192.168.1.117:5055/api/health` from the Pi = 200.
- Edge through the Pi-only path: killed the PC connector (exact PID 16300)
  and `https://aali.dpdns.org/api/health` kept answering (200) via the Pi.
- Real ask end-to-end with the master key: reply came back through
  edge → Pi tunnel → catcher → PC API → brain.
- SSE `/api/ask/stream` events (activity/scratchpad/done) flow through the Pi.
- Wake emission: a throwaway catcher aimed at a dead port logged
  `wake packet sent to 192.168.1.117: ok` (real magic packet, real NIC MAC).
- NOT yet tested (needs the owner's go): the actual S3 sleep → wake round trip.

## PC-side one-time arming (ADMIN)

```bat
scripts\wol_enable_pc.bat   (Run as administrator)
```
- NIC `Wake on Magic Packet` was already Enabled (Intel I219-V, F4-B5-20-46-44-27);
  the script re-arms idempotently and adds the LAN-only UDP 9 firewall rule.
- `powercfg /a` must list `Standby (S3)` — verified available on this PC.
- Optional sleep-on-idle (opt-in): watch `sleep_guard.log` from
  `.venv\Scripts\python.exe scripts\idle_sleep_guard.py --loop` for a few days,
  then set `powercfg /change standby-timeout-ac 15`. The guard blocks sleep when
  training machinery, unexpected GPU compute, or user sessions are active.

## Pi-side layout (no root used anywhere)

```
/home/aalici/aali_sentinel/
  pi_sentinel_catcher.py   # the catcher (stdlib only)
  run_catcher.sh           # exec wrapper with PC_IP/PC_MAC baked in
  cloudflared              # linux-arm64 binary (2025.6.1)
  config.yml               # tunnel aali → http://127.0.0.1:8766
  81d534db-….json          # tunnel credentials (copied from the PC)
  catcher.log / tunnel.log # content-free logs
```
Crontab (installed idempotently by `scripts/pi_sentinel/deploy_to_pi.sh`):
```
@reboot    run_catcher.sh                       # start catcher at boot
@reboot    cloudflared tunnel … run aali        # start tunnel at boot
* * * * *  pgrep-gated revive for each          # crash watchdog, minute cron
```
The `aalici` user needs NO sudo for any of this (Debian cron runs user
crontabs without login; no linger dependency).

## Operations

- Redeploy/refresh: `bash scripts/pi_sentinel/deploy_to_pi.sh`
- Catcher liveness: from the Pi, `curl http://127.0.0.1:8766/_sentinel/ping`
  (local-only endpoint; never proxies, never wakes the PC).
- A sleeping PC answered honestly: `/api/health` → **409** with a bilingual
  "waking, retry in ~30 s" body; real requests during the wake window → **502**
  with the same hint + `Retry-After: 15`.
- Roll back to PC-hosted tunnel: `Enable-ScheduledTask 'HWK TunnelAutoStart'`,
  restart the PC connector (`scripts\tunnel_autostart.bat`), then remove the
  Pi crontab lines (`crontab -e` on the Pi).
- Tests: `tests/test_pi_sentinel_catcher.py` (15, in-process real servers,
  wake_fn always a recorder) + `tests/test_idle_sleep_guard.py` (12, fabricated
  process tables; the soup.exe shim → bare-python ancestry case is pinned).

## Honest limits

- First message after a sleep costs ~1–3 min (PC boot + brain load); the
  409/502 bodies tell the client to retry instead of hanging.
- The catcher trusts the LAN: it forwards to a fixed PC IP and copies only an
  allow-list of headers; it adds no auth of its own (the PC's key gate remains
  the boundary).
- Response bodies are capped at 8 MB (honest truncation marker appended).
- While the PC is asleep, only health is answered locally — every other
  request pays the wake cost.
