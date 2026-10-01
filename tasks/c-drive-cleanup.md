# C: drive emergency cleanup (100% full)

- owner: buffy (this PC, Freebuff session)
- status: in-progress
- started: 2026-10-01

## Why
C: is at 100% (2.2G free as of the v7 morning report) — not a rule drive,
but Windows misbehaves when full. Owner said GO to clean it.

## Rules for this task
- Inventory FIRST, delete second. Never touch anything on D:/X: rule drives.
- Unambiguously-safe cache/temp purges directly; anything else (hiberfil,
  installers, personal files, big dirs) → measure, then ASK the owner.
- Prefer RELOCATING caches to D: over deleting when it preserves function
  (pip/HF/npm caches support env-var redirects).
- Never kill processes by guess; stop services only by exact name with
  owner consent.

## Plan
1. Measure per-drive free space + top C: consumers (User AppData, Temp,
   caches, Windows dirs, pagefile/hiberfil presence).
2. Purge safe regenerating caches (pip, npm, Temp, Windows Temp, thumbcache…).
3. Propose relocations (pip cache → D:) and anything owner-gated.
4. Report freed space; update AGENTS.md status.

## Status log
- 2026-10-01 investigation started.
