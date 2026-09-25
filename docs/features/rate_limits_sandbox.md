# Rate-limit review + sandbox pentest (Part 5.4–5.5 — 2026-09-25)

## 5.4 — Rate-limit review (what exists, what was missing, what's fixed)

| Surface | Limit | Key | Status |
|---|---|---|---|
| `/api/register` (key signup) | N/day per IP | IP | pre-existing (apikeys.signup) |
| `/api/search` | 30/min | caller ns | pre-existing |
| `/api/voice/synthesize` | 30/min | user_id | pre-existing |
| issued keys / sessions | daily request cap | key_id | pre-existing |
| `/v1/chat/completions` | metering per key | key_id | pre-existing |
| **`/api/auth/login`** | **5 fails → 5-min lockout** | account email | **ADDED (was unbounded)** |
| **`/api/auth/verify`** | **5 wrong codes burns the pending record** | pending signup | **ADDED (was unbounded)** |
| **`/api/auth/reset_confirm`** | **5 wrong codes burns the reset request** | pending reset | **ADDED (was unbounded)** |

Design notes:
- The lockout is **in-memory** (resets on restart) on purpose: it is the
  second line behind key/session gates and must never permanently lock
  the owner out of their own machine.
- A lockout rejects even the **correct** password — that is what a
  lockout is. Success clears the failure counter.
- 6-digit code guessing is now bounded to 5 tries per TTL window
  (1,000,000 combos / 5 = 200k effective guesses per ~10-min window, and
  each burn requires a fresh request) instead of unbounded.
- Unknown emails and wrong passwords return the SAME message (no user
  enumeration).
- Limiter dicts are per-process and small (auth buckets keyed by email,
  cleaned on success) — no unbounded-growth exposure added.

## 5.5 — Sandbox pentest (`tests/test_sandbox_pentest.py`)

Adversarial suite against `file_agent/file_agent/file_tools.py`, run on
temp workspaces only. **Four real findings, all fixed:**

1. **`run_command` flag-driven cwd escapes** — `git -C ../.. status`,
   `npm install --prefix ../../evil`, `pip install --target D:/x`
   executed OUTSIDE the workspace (the cwd is the sandbox; these flags
   move the effective working dir). Fixed: `-C/--git-dir/--work-tree/
   --prefix/--target/--out/--outdir/--output/-o/--directory` arguments
   are resolved and must stay inside the workspace. (First attempt had a
   case bug — `-C`.lower() vs part `-C` — the pentest caught the guard
   NOT firing; fixed to lowercase both sides.)
2. **Windows device names** — `write_file("NUL")` silently succeeded
   (writes to NUL vanish; CON opens the console). Fixed: CON/PRN/AUX/
   NUL/COM1-9/LPT1-9 refused by name in `_resolve` (any segment, before
   resolution).
3. **Encoded-path payloads** — `%2e%2e%2f`, `%5c`, `\x2e` strings became
   odd filenames inside the sandbox (decoy landmines). Fixed: refused by
   pattern in `_resolve`.
4. **Drive-letter relatives** (`C:/x` as a "relative" path) — refused
   explicitly now (was accidental via relative_to).

Confirmed already-solid (attacks that FAIL):
- classic `../` traversal, deep/mixed separators, `....//` variants
- absolute paths, UNC (`\\server\share`), device-namespace (`\\.\C:`)
- NTFS alternate data streams (`file.txt:hidden`)
- symlink planted inside the workspace pointing outside (resolve
  follows it, relative_to refuses) — skipped only where the host lacks
  symlink privilege
- type confusion (None/int/bytes/list/dict as path), move/delete
  destination escapes

## Tests

`tests/test_sandbox_pentest.py` (31): 8 parametrized path escapes ×
read+write, device names, drive/UNC/ADS, symlink escape, type confusion,
move/delete escapes, 3 command-flag escapes + inside-workspace allowed,
login lockout (incl. correct-password-while-locked + counter clear),
verify/reset code-guessing bounds.
