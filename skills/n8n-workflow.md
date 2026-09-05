---
name: n8n-workflow
description: When the user wants an automation, integration, or "do X every time Y happens" — build it as an n8n workflow instead of a one-off script.
---

## When to use this

Use this whenever the user describes a repeating or triggered task: "when I get an email from X, do Y", "every morning check Z and send me a message", "connect A to B automatically". n8n workflows are visual, editable by the user afterward, and don't need you to keep a background process running yourself.

## How

1. Call `make_n8n_workflow` with a clear natural-language `description` of the trigger and the actions. Be specific about the trigger type in your description (webhook / schedule / email) — it maps directly to the node the tool picks.
2. The tool writes an importable `.json` workflow file under `n8n_workflows/` and returns `import_steps` and (for webhook triggers) a `webhook_note` with the exact local URL.
3. Relay those import steps to the user verbatim — don't paraphrase the localhost URL, a typo there breaks the workflow.
4. If the user asks to change something in an existing workflow, prefer regenerating it with an updated description over hand-editing the JSON — the generator keeps node names and structure consistent.

## What not to do

Don't build a scheduled task as a Python script with a `while True` loop and `time.sleep` — that only runs while you happen to be running, and the user can't see or edit it. n8n runs independently and gives them a UI.

Don't invent node types or webhook paths — always go through `make_n8n_workflow` so the JSON stays valid and importable.
