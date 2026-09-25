# Prompt library (Wave 3 #9 — 2026-09-25)

Built-in starter prompts (Arabic-first, English second) + per-user
custom prompts. The UI **inserts** a prompt into the composer — it never
auto-sends; the user reviews and presses Enter.

## Model

```
GET    /api/prompts          -> {builtin: [...], custom: [...]}
POST   /api/prompts          {title, body}        -> 201 (custom)
DELETE /api/prompts/<pid>                          -> ok (own only)
```

- **12 builtins** (7 AR + 5 EN): summarize a file, build an app, review
  code, translate, draft email, action plan, spreadsheet — each with
  icon + title + body template ending in an insertion point.
- **Custom prompts**: ns-scoped (`u<key_id>` / local — same contract as
  projects/shares), ≤100 per ns, title ≤80 chars, body ≤4000 chars.
  Arabic-first errors; guests uniform-404.
- Store: `D:/hwk-data/prompt_library.jsonl` (custom only; builtins ship
  in code so they never break and never need migration).

## Clients

- **Web**: ✦ button in the composer → library dialog (custom section
  with delete, AR section, EN section, add form). Inserting closes the
  dialog and fills the composer.
- **CLI**: `/prompts` (numbered list, ✦ = custom) → `/prompts use <n>`
  prints the body to complete; `/prompts add <title>` + paste (end with
  a lone `+++`); `/prompts del <id>`.

## Tests

`tests/test_shares_prompts.py`: builtins always present (AR+EN), custom
add/list/delete ns-scoped (another ns cannot delete mine), empty
rejection, per-ns cap, endpoint round-trip, guest 404, bad-add 400.
