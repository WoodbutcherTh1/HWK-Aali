# آلي Monitor — مراقب أعمال آلي (2026-09-25)

The owner-ordered desktop companion: a tray + window app that watches
Aali's long jobs from the REAL logs in `D:/hwk-data` and notifies when
something needs attention. Content-free by design — state, numbers and
alert lines only; never chat or file content.

## What it watches

| Signal | Source | Alert condition |
|---|---|---|
| Aali API :5055 | live HTTP `/api/health` | not answering → 🚨 |
| Aali brain :20129 | live HTTP `/v1/models` | not answering → 🚨 |
| GPU | `nvidia-smi` (same parse as mission_clock) | temp ≥ 85°C → 🥵 |
| Phase D trainer | `phase_d_train.log` (step/total, `done:`) | traceback tail → 🚨 |
| Graduation pipeline | `soup_pipeline.log` verdict lines | NO-GO → ❌ |
| D: free space | `shutil.disk_usage` | < 20 GB (repo rule) → 💾 |

All parsers are PURE (text in → state out, I/O injectable) and live in
`aali_monitor/core.py`; the GUI in `aali_monitor/shell.py` mirrors the
proven `aali_node/shell.py` mechanics (pywebview window + pystray tray,
hide-to-tray while the watcher lives, honest headless fallback).

## Run it

```bat
:: from the repo root, with the monitor venv:
.venv-monitor\Scripts\python.exe -m aali_monitor

:: headless (console alerts only):
.venv-monitor\Scripts\python.exe -m aali_monitor --headless

:: one snapshot as JSON, then exit:
.venv-monitor\Scripts\python.exe -m aali_monitor --once
```

Window close = hide to tray (the watcher keeps running); quit from the
tray menu (إظهار / إنهاء المراقب) or `--headless` Ctrl+C.

## Dependencies (isolated, per repo rules)

`pywebview`, `pystray`, `Pillow` — ONLY in `.venv-monitor` (create with
`python -m venv D:/hwk-tools/monitor-venv` or a repo-local
`.venv-monitor`; `pip install pywebview pystray Pillow`). The training
venv `.venv` never gains these. No GUI backend installed? `run_monitor`
falls back to a headless poller with the same alerts on stderr.

## Tests

`tests/test_monitor_core.py` (24): every parser against the real log
shapes (tqdm `step/total [..]`, `done: eval=... tokens=...`, VERDICT
PROMOTE/NO-GO), GPU comma-name parsing, injected HTTP probes, Arabic
alert lines, snapshot end-to-end with fakes, alert dedup + re-arm in the
poll loop, `--once` JSON output. The suite never touches the live logs,
GPU or network.
