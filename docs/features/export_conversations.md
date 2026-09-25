# تصدير المحادثات — Export Conversations (Wave 1 #1)

_Shipped 2026-09-25 · owner-approved Wave 1 · feature gap closed from the
Part 9.1 audit (was falsely marked DONE — it did not exist before this)._


## What it does

Every conversation can now be downloaded from the sessions sidebar:

- **⬇ button** → Markdown export (default, readable, Arabic-first headers)
- **{ } button** → JSON export (faithful record dump for backups/tools)

The file downloads as `aali-session-<sid>.md|.json`. Content is emitted
as-is, so exported chats stay copy-pasteable Markdown.

## API

`GET /api/session/<sid>/export?format=md|json|pdf`

| format | Result |
|---|---|
| `md` (default) | `text/markdown` attachment, RTL Arabic section headers |
| `json` | `application/json` attachment: `{sid, created_at, updated_at, turns[]}` |
| `pdf` | `400` honest error until implemented (no fake PDFs) |

Unknown/foreign sessions → `404`. Key mode → `401` without credentials.

## Security & scope

- Read-only; inherits the global `before_request` key gate.
- `_user_ns()` isolation holds: a user can only export their own
  conversations (test-pinned).
- The web client fetches as a **blob** — the API key never lands in a
  URL, history, or the downloaded filename.

## Files

- `file-agent/app.py` — `_export_markdown()` + `api_session_export()`
- `tests/test_export.py` — 8 tests (md/json/pdf/404/401/isolation/roles/empty)
- `web/src/api.ts` — `exportSession()` (blob download)
- `web/src/App.tsx` — ⬇ and { } buttons beside 🗑 in the sessions sidebar

## PDF (later)

Will be added without new deps via print-to-PDF or a pure-Python writer;
the endpoint already reserves the contract (400 + honest message).
