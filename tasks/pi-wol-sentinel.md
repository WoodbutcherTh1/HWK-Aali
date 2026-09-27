# Pi WoL sentinel — PC sleeps, Pi answers and wakes it

owner: buffy
status: done
started: 2026-09-27
finished: 2026-09-27

## Goal

Owner question: "can the Pi host Aali so this PC doesn't stay on 24/7?"
Verdict: Pi 3 B+ can never host the brain (torch SIGILL, 1 GB RAM — 09-23
verdict stands), but the Pi CAN take the tunnel + wake the PC on demand.
This task implemented that: Path A of the 2026-09-27 options.

## Shipped

- `scripts/pi_sentinel_catcher.py` — Pi-side catcher (stdlib only):
  streaming reverse proxy → PC:5055, local 409 health while asleep,
  WoL magic packet (directed + broadcast) with a 45 s cooldown,
  `/_sentinel/ping` local-only probe, content-free logging (log_request
  override — the framework default leaks raw query strings; caught by
  test_logs_are_content_free).
- `scripts/idle_sleep_guard.py` — PC-side sleep decision (exit 0/1,
  NEVER sleeps by itself): training.log heartbeat + machinery fragments +
  unexpected GPU compute PIDs + user-session processes; recognizes the
  always-on stack by cmdline OR ancestry (soup.exe shim → bare python
  child, live-proven PID 113112 case) with slash-normalized fragments.
- `scripts/wol_enable_pc.bat` — one-time ADMIN arming: WoL re-arm,
  LAN-only UDP 9 firewall rule, powercfg /a reminder (CRLF-clean).
- `scripts/pi_sentinel/{run_catcher.sh,deploy_to_pi.sh}` — Pi runner +
  idempotent deploy (catcher, ARM64 cloudflared 2025.6.1, config.yml,
  tunnel credentials copy, @reboot + minute-cron pgrep-gated revives).
- Docs: docs/pi_wol_sentinel.md. Tests: test_pi_sentinel_catcher.py (15)
  + test_idle_sleep_guard.py (12).

## Live verification (2026-09-27 evening)

- Pi→PC API over LAN: 200. Edge via Pi-only path after killing the PC
  connector (exact PID 16300): health 200 twice.
- Real ask with master key through edge→Pi→catcher→PC→brain: reply ok.
  SSE stream events flow. Anonymous asks 404 = the server's key gate
  working as designed (mode: local + key gate).
- Real magic packet emitted by a throwaway catcher aimed at a dead port
  ("wake packet sent: ok"). NIC already had Wake on Magic Packet Enabled;
  S3 standby available.
- CUT OVER: HWK TunnelAutoStart DISABLED, PC cloudflared killed;
  tunnel now: Pi connector (always on) + catcher. Crontab installed on
  the Pi (2 @reboot + 2 watchdog lines).

## Next (owner decisions, not done here)

1. Sleep the PC once for the real S3 wake test (everything else proven).
2. Opt-in sleep-on-idle: run idle_sleep_guard --loop for a few days,
   then powercfg /change standby-timeout-ac 15.
3. Optionally re-enable HWK NightCaretaker when a graduation run is
   wanted again (disabled this session with owner approval).

## Lessons

- Fixture named `catcher` shadowed the `import pi_sentinel_catcher as
  catcher` module alias — pytest fixtures are module-level names too.
- BaseHTTPRequestHandler.log_request calls log_message with the RAW
  request line: overriding log_message alone still leaks query strings.
  Override log_request and log only a trimmed path.
- A bash-started process shows FORWARD slashes in its cmdline
  (file-agent/app.py) while cmd-started shows backslashes — normalize
  both sides before fragment matching.
- The venv python shim PID-pair kills the parent when the child dies
  (re-confirmed); likewise, classify GPU PIDs by ancestry, not cmdline
  fragments alone — the brain's real python is a BARE python.exe child.
