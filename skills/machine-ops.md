---
name: machine-ops
description: Operating the owner's Windows PC through machine_ops — opening apps, installing/uninstalling software (winget/npm), listing and stopping processes, reading live system stats. Confirmation rules for irreversible actions included.
---

## The tool

One tool, seven actions: `machine_ops(action, ...)`.

| action | what it does | needs force? |
|---|---|---|
| `open` | open an app (.exe), file, folder, or URL with the OS handler | no |
| `system_info` | read GPU / RAM / disks / CPU load live | no |
| `list_processes` | list running processes (filter by name) | no |
| `search_software` | find the right winget/npm package id (read-only) | no |
| `install` | install software via winget or npm | **YES** |
| `uninstall` | remove software | **YES** |
| `kill_process` | force-stop a program (by name or PID) | **YES** |

## The confirmation rule (never break it)

For the three forced actions, the order is fixed:

1. `search_software` first, so you install the right package id.
2. Tell the user exactly what will happen: "سأثبّت X (رقم الحزمة Y) عبر winget —
   هل توافق؟"
3. Only after an explicit yes in chat, call with `force: true`.
4. Verify afterwards (list_processes / system_info) before claiming success —
   the self-verification skill applies here too.

If the user is vague ("ثبّت اللي تحتاجه"), name what you plan to install and
wait. A wrong install wastes their trust; one question never does.

## Protected runtimes

Aali's own training and agent processes (python.exe / node.exe images) are
**protected from kill by name** — the tool refuses, even with force. If the
user asks to stop training, check which process owns it
(`list_processes --name python`), tell them the PIDs, and let them decide on
an exact PID — a wrong kill once destroyed a 12-hour training run.

## Worked examples

- "افتح لي المفكرة" → `machine_ops("open", target="notepad.exe")` — no confirmation needed.
- "كم مساحة فاضية بالجهاز؟" → `machine_ops("system_info")` → report the real numbers.
- "ثبّت لي 7zip" → `search_software("7zip")` → "سأثبّت 7zip.7z عبر winget، توافق؟"
  → on yes: `machine_ops("install", target="7zip.7zip", force=true)` → report exit status.
- "كروم مهنج، سكّره" → `list_processes("chrome")` → name the PIDs → on yes:
  `kill_process(pid=..., force=true)` → verify it's gone.
