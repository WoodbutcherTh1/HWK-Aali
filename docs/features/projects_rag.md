# Projects + RAG knowledge base (Wave 2 #5+#6 — 2026-09-25)

The organizing layer: a **project** groups conversations, carries a
custom instruction (its "system flavor"), and holds a per-project
**knowledge base** the brain answers from — with citations. Retrieval is
BM25 via SQLite FTS5 — the same machinery as conversation search, zero
new deps, no embeddings (banned until the own-embedding-model phase per
`priority_plan.md`).

## The model

```
project (name, instruction, ns)
   ├── conversations  (a session binds ONE active project)
   └── knowledge base (docs -> chunks -> FTS5 index)
```

- **Instruction**: prepended to every ask in the session as
  `[تعليمات المشروع: …]` — the project's standing guidance.
- **Knowledge base**: text documents (≤200k chars each, ≤100 per
  project), chunked with overlap (700 chars / 80 overlap) only when big
  enough to need it — a short doc stays ONE chunk.
- **Retrieval**: precision-first. AND-all-tokens; when nothing matches
  (a query word absent from the docs — normal for questions) it falls
  back to OR-ranked BM25. Score gate: `bm25() <= 0` = at least one real
  term match. Empty knowledge base / no match → **no context, no fake
  citations** — the ask proceeds and the reply carries an honest
  `rag_note` when docs exist but nothing matched.

## API (all `_require_role("projects")` — guests get the uniform 404)

| Route | Body/Params | Returns |
|---|---|---|
| `GET /api/projects` | — | projects (newest first, doc counts) |
| `POST /api/projects` | `{name, instruction?}` | 201 + project |
| `GET/PATCH/DELETE /api/projects/<pid>` | partial `{name?, instruction?, archived?}` | project / 404 |
| `GET/POST /api/projects/<pid>/docs` | `{name, content}` | docs list / 201 + doc |
| `GET/DELETE /api/projects/<pid>/docs/<did>` | — | full content / ok |
| `POST /api/projects/<pid>/retrieve` | `{query, top_k?}` | ranked chunks + scores |
| `GET /api/projects/active?sid=` | — | the session's active project |
| `POST /api/projects/active` | `{sid, project_id\|null}` | bind/unbind |

## Ask-time behavior

With a project bound to the session, every ask (JSON and stream):

1. retrieves the top-4 chunks for the user's message,
2. injects them as a **clearly-labeled reference block** (`سياق مرجعي من
   قاعدة معرفة المشروع …`) after the message — data, never commands, and
   the model has no tool access to the knowledge base itself,
3. prepends the project instruction,
4. returns `rag_sources: [{doc_id, doc_name, chunk, snippet}]` in the
   reply body → the UI renders **📚 من قاعدة المعرفة** citation chips.

Deleted/archived project while bound → the session detaches silently
and keeps working (verified by test).

## Isolation

- Every project belongs to the ns that created it (`u<key_id>` /
  local `""`); retrieval ALWAYS filters project_id + ns — one user's
  knowledge base can never leak into another's context (test-pinned).
- Knowledge-base chunks live in `D:/hwk-data/projects/aali_projects.db`,
  entirely separate from the conversation-search index (test-pinned).

## Clients

- **Web**: 🗂️ المشاريع nav item → full manager dialog (create/archive/
  delete, instruction editor, doc list + add/remove, «💬 استخدام في
  المحادثة»); gold project chip in the chat header (click to detach);
  citation chips under replies.
- **CLI**: `/projects list|new|use|off|docs|add` (`add` pastes text,
  terminate with a lone `+++` line).

## Tests

`tests/test_projects.py` (25): store CRUD + ns isolation + Arabic
errors, chunking shape (caps, overlap, single-chunk small docs), AR+EN
retrieval, cross-project leak-proofing, malformed-MATCH safety, honest
empty context, endpoint CRUD/404 contract, session binding, ask-time
injection with sources, honest no-hit note, deleted-project detach,
search-index separation.
