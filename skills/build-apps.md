---
name: build-apps
description: Building apps and games for the user end-to-end — scaffolding, installing dependencies, running and testing with run_command, and fixing errors from real output before claiming success.
---

## Polished deliverables (2026-09-23 toolbelt)
- **create_artifact** — the FINAL result of report/table/demo requests ships
  as one shareable file (styled RTL HTML page or Markdown). Gather content
  first, then one call, then give the path.
- **write_excel / read_excel** — real spreadsheets: verify data first, write,
  then read_excel back to confirm before telling the user it is done.
- **create_plot** — charts ONLY from numbers you actually computed or fetched
  this conversation; never invent points; give the PNG path.
- **diff_files** — before editing a file you have not read, or when the user
  asks "what changed?": diff first, then edit.
- **todo_plan** — any task with 3+ steps: add the steps, work top to bottom,
  update statuses as you go. The checklist lives in .aali-plan.json.
- **recall_search** — after web_search: pull the k most relevant passages
  from the best URLs and answer ONLY from those, citing the URL.
- **screenshot** — ONLY on explicit user request (native confirm, guest-
  blocked). Read the capture with read_image to answer about it.

## The workflow (never skip the order)

1. **Clarify the shape** — one question max: what kind (game / web app / CLI tool),
   language preference, one must-have feature. Do not interrogate; small apps
   are built, not specified to death.
2. **Plan small** — name the 3–6 files you will create and the run command
   before writing anything.
3. **Write the code** with write_file (paths relative to the workspace, always).
4. **Install dependencies** with run_command: `pip install -r requirements.txt`
   or `npm install`. Tell the user what you are installing first (supply-chain
   rule: official registries only, no paste-and-run).
5. **Run it** with run_command: `pytest -q` for testable code, `python game.py`
   with a smoke timeout, or `python -m http.server` / `npx serve` for web apps,
   then fetch/read back to verify it serves.
6. **Fix from real output** — the error text you actually saw, not the one you
   imagined. One fix per iteration, re-run after each.
7. **Report honestly** — what you built, how to run it, what you verified with
   real output, and anything NOT verified. A failed run is reported as a failed
   run with its error.

## Good default shapes (small wins first)

- Single-file python game with pygame (snake, pong, flappy) — runs with
  `python game.py`.
- Single-file HTML+JS game or app — open in the browser via machine_ops open,
  no server needed.
- CLI tool in python with a `--help` — testable with run_command instantly.
- Small web page/app — one index.html (+ style.css, script.js), served and
  verified before handover.

Do NOT scaffold heavy frameworks (Next.js, Unity, Electron) unless the user
asked for exactly that — they exceed the workspace-sandboxed command set's
comfort zone and take far longer to verify.

## Hard rules

- Everything stays inside the workspace: relative paths only.
- Read-only commands freely; anything destructive or system-wide: explain +
  explicit yes first (machine-ops rule).
- Never claim "it works" without having RUN it and seen the real exit status.
- If run_command is disabled (HWK_ALLOW_COMMANDS=0, guest policy), say so
  honestly and deliver the code with run instructions instead.

## Worked examples

- "اصنع لي لعبة ثعبان" → plan (game.py, pygame) → `pip install pygame` →
  write → `python game.py` smoke run → report with the real output.
- "Build me a todo web app" → index.html/style.css/script.js →
  `python -m http.server 8123` → fetch the page to verify → report URL.
- "Can you run commands for me?" → yes: run_command allow-list (python, pip,
  node, npm, git, compilers), sandboxed to the workspace, real output reported.
