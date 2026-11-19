# Aali Engine API

Status: **v1 contract — local engine** · 2026-11-06

This document is the contract between the Aali engine (`file-agent/`) and every
client (web, Desktop, Studio, CLI, iOS, Hub/Node). It covers sessions,
streaming, tool proposals, approval, file diffs and surface routing.

It describes **implemented behavior**, not a roadmap. UI work is intentionally
out of scope: clients consume these endpoints and events, and may animate them
however they like.

---

## 1. Principles

1. **One brain, many clients.** All clients talk to the same engine. No client
   owns file access or model state.
2. **The agent proposes; the human applies.** In an agent turn, mutating text
   tools never touch the real file. They create a reviewable proposal.
3. **The server is the trust boundary.** Sandbox resolution, guest policy,
   approval authorization and conflict checks happen server-side. Client hints
   are never trusted.
4. **Evidence, not promises.** A tool result is the only proof an operation
   happened. A pending proposal is not an applied edit.
5. **Stable core, additive events.** Existing fields remain; new event kinds
   are additive and ignorable.

---

## 2. Transport and authentication

- Base URL (default local engine): `http://127.0.0.1:5055`
- JSON requests: `Content-Type: application/json`
- Streaming: `text/event-stream` (SSE), one JSON object per `data:` line.
- Authentication, in precedence order:
  - `X-API-Key: <key>` — master key or an issued key.
  - `X-Session-Token: <token>` — account session.
  - Local loopback may run unauthenticated when the engine has no master key.
- Remote guests are allowed only on the guest-chat surface. Approval is a
  local/owner action and is refused (uniform `404`) for remote guests.

Standard success/error shape:

```json
{"ok": true, "reply": "...", "sid": "..."}
```

```json
{"ok": false, "error": "human-readable reason"}
```

---

## 3. Sessions

A session is a bounded conversation history keyed by `sid`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ask` | One turn, JSON response |
| `POST` | `/api/ask/stream` | One turn, SSE events + final `done` |
| `GET` | `/api/sessions` | List sessions visible to the caller |
| `GET` | `/api/session/<sid>` | Read a session's turns |
| `DELETE` | `/api/session/<sid>` | Delete a session |
| `POST` | `/api/compact` | Fold older turns into a summary |

`POST /api/ask` body:

```json
{
  "message": "user text",
  "sid": "optional; generated when absent",
  "mode": "local",
  "policy": "auto",
  "confirm": false,
  "provider": "auto",
  "attachments": ["stored-name.png"],
  "links": ["opaque-token"]
}
```

`links` are opaque tokens from `/api/link`; page text never crosses the client
as content. `attachments` are stored names re-validated server-side.

Response extras (present only when relevant):

```json
{
  "ok": true,
  "reply": "...",
  "sid": "...",
  "needs_confirm": true,
  "pending_action": {"tool": "run_command", "arguments": {}},
  "proposal": {"proposal_id": "...", "path": "...", "tool": "write_file", "status": "pending_approval", "diff": "..."},
  "surface": {"surface": "studio", "reason": "multi-file refactor"},
  "rag_sources": [],
  "suggestions": []
}
```

---

## 4. Streaming contract

`POST /api/ask/stream` returns SSE. Event names are stable; clients must ignore
unknown events.

| SSE event | When | Key fields |
|---|---|---|
| `activity` | model/tool progress | `event`, `tool`, `arguments`, `result`, `provider`, `model` |
| `scratchpad` | compact running log | `content`, `lines` |
| `cot` | optional reasoning view (verbose only) | `iteration`, `text` |
| `proposal_created` | an edit is staged | `proposal_id`, `path`, `tool`, `diff` |
| `SURFACE_SWITCH` | the agent routes to another product surface | `surface`, `reason` |
| `done` | terminal body | `ok`, `reply`, `sid`, optional `proposal` / `surface` / `needs_confirm` |

Example:

```text
event: SURFACE_SWITCH
data: {"event":"SURFACE_SWITCH","surface":"studio","reason":"complex multi-file refactor"}

event: proposal_created
data: {"event":"proposal_created","proposal_id":"9f2c...","path":"src/app.py","tool":"replace_in_file","diff":"--- src/app.py (before)\n+++ src/app.py (after)\n@@ ..."}
```

The stream may emit keep-alive comments (`: keep-alive`) during long model
turns. A client must not treat comments as events.

---

## 5. Proposal-first edits

### 5.1 What changes

Inside an agent turn, these tools are **staged**:

- `write_file`
- `append_file`
- `replace_in_file`

They return:

```json
{
  "status": "pending_approval",
  "proposal_id": "9f2c1d4e7a0b3c5d",
  "path": "src/app.py",
  "tool": "replace_in_file",
  "diff": "unified diff",
  "bytes": 1234,
  "message": "..."
}
```

The real file is **not modified**. The engine emits `proposal_created` and
halts the turn with a review message.

Direct human/tool writes (Studio's Save button, the Node sandbox, tests,
generated artifacts) run outside proposal mode and keep their immediate-write
contract. Proposal mode is per agent run, thread/context-local, and can be
disabled explicitly with `AALI_PROPOSAL_MODE=0`.

### 5.2 Storage

Inside the workspace:

```text
.aali/staging/<proposal_id>.json     metadata
.aali/staging/<proposal_id>.before   exact pre-edit text (absent for new files)
.aali/staging/<proposal_id>.after    exact proposed text
```

Metadata contains hashes, path, tool, timestamps, status and the rendered
diff. The staging directory is excluded from `list_files`, `search_files` and
the repository map. It never stores content outside the workspace.

### 5.3 Approval endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/approve` | Apply or reject one proposal |
| `POST` | `/api/reject` | Reject one proposal (explicit alias) |
| `GET` | `/api/proposals` | List proposals (newest first) |
| `GET` | `/api/proposals/<id>` | Read one proposal |

`POST /api/approve`:

```json
{"proposal_id": "9f2c1d4e7a0b3c5d"}
```

Apply (`"approve": true`, default):

- Re-reads the file and requires it to still equal the exact `before` content.
- On mismatch returns **409** and writes nothing. The safe response is to
  re-run the edit against the current file, not to force it.
- On success writes atomically, marks the proposal `applied`, and deletes the
  `.before` / `.after` payloads.

Reject (`"approve": false` or `"reject": true`):

- Marks the proposal `rejected`, records an optional `reason`, deletes the
  payloads, and writes nothing.

`GET /api/proposals?status=pending&limit=20`:

```json
{
  "ok": true,
  "proposals": [{"proposal_id": "...", "path": "...", "status": "pending", "diff": "..."}],
  "pending": 1,
  "total": 4
}
```

### 5.4 Diff format

Diffs are unified diffs (`difflib.unified_diff`) with `before` / `after`
labels. They are display data, not a patch format; clients must not apply them
directly. The authoritative candidate is `<id>.after`, and only
`/api/approve` may write it.

### 5.5 Security rules

- Paths are resolved by the existing workspace sandbox (`file_tools._resolve`):
  absolute paths, drive paths, device names, encoded traversal and escapes are
  refused.
- Proposal ids are 16 lowercase hex characters; malformed ids are `404`.
- Approval is allowed only from loopback or an admin-authenticated caller.
  Remote guests receive `404`.
- Apply is conflict-checked; there is no force-overwrite option.
- The engine never writes outside the workspace and never applies a proposal
  implicitly.

---

## 6. Surface router

The agent can route a turn to a product surface with the `switch_surface` tool.

```json
{
  "surface_name": "chat | studio | graphics | control",
  "reason": "one short sentence"
}
```

Semantics:

- `studio` — complex coding, multi-file edits, refactors, tests, diffs, IDE work.
- `graphics` — images, design, charts, visual assets, media editing.
- `control` — automating the owner's PC, apps, processes, files, system tasks.
- `chat` — ordinary questions and short answers (the default).

The tool is a **routing hint**, not a capability grant. It changes nothing on
disk and starts no process. An unknown surface is refused, so a model cannot
invent a UI state the client does not implement.

When called, the engine emits:

```text
event: SURFACE_SWITCH
data: {"event":"SURFACE_SWITCH","surface":"studio","reason":"multi-file refactor"}
```

and includes the same object in the `done` body and `/api/ask` response as
`surface`. Clients listen for `SURFACE_SWITCH` and transition smoothly; a
client that does not implement surfaces can ignore the event and keep the chat
view.

---

## 7. Repository map

`file_agent/repo_map.py` exposes:

```python
generate_repo_map(workspace_path: str | Path, max_tokens: int = 4000) -> str
```

- Honors the root `.gitignore` (and `.git/info/exclude`).
- Skips VCS, dependency, build and `.aali` directories.
- Parses Python and TypeScript/JavaScript classes, functions, interfaces,
  types and imports.
- Uses optional `tree-sitter` when installed; otherwise a tested regex parser.
- Never follows a symlink outside the workspace, skips NUL/binary files and
  caps a file at 512 KB.
- Returns a deterministic, token-budgeted summary. Over-budget files are
  omitted with an explicit count.

This is the engine's project-awareness primitive. It is not yet injected into
every ask; clients/services may call it directly or the engine may include it
in a future context layer without changing this contract.

---

## 8. Error and status codes

| Status | Meaning |
|---|---|
| `200` | Success |
| `400` | Invalid input, malformed proposal, or an edit that cannot be staged |
| `404` | Unknown session/proposal, or an unauthorized approval surface |
| `409` | Proposal conflict: the file changed since staging |
| `429` | Guest chat cap reached |
| `500` | Model/engine failure; `reply` starts with `[خطأ]` |

Proposal error codes are stable strings in `error`; clients should show the
message and offer a retry/review path rather than guessing.

---

## 9. Compatibility and non-goals

- `POST /api/ask` and `GET /api/health` remain the stable minimal contract.
- New SSE events are additive. Clients must ignore unknown events.
- The canonical router is `file_agent.model_router`; the duplicate
  `file-agent/model_router/` package was removed. Frozen builds put
  `file-agent/` on `sys.path` and import the canonical package.
- Not included in v1: a review UI, remote/Node proposal approval, multi-user
  proposal namespaces (proposals are workspace-scoped), and automatic repo-map
  prompt injection. These are separate product steps and are intentionally
  not promised here.
