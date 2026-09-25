# البحث في المحادثات — Conversation Search (Wave 1 #2)

_Shipped 2026-09-25 · owner-approved Wave 1 · closes the "conversation
search: PARTIAL" gap from the Part 9.1 audit._


## What it does

Instant full-text search across every stored conversation — web (Ctrl/⌘+K
modal), CLI (`/search`), and a plain HTTP API. Arabic and English, with
prefix matching (`بر` finds `بره`).

## Architecture

- **SQLite FTS5** (stdlib `sqlite3` — no Elasticsearch/Meilisearch, per rules)
  at `D:/hwk-data/search/aali_search.db`, WAL journal mode.
- Virtual table `messages`: `content` (tokenized) + UNINDEXED metadata
  `ns, sid, role, ts, turn_idx, project_id`.
  - `project_id` is reserved for Wave 2 Projects — present now so adding
    projects never forces an index rebuild.
- `tokenize='unicode61'` — correct Arabic word tokenization, no stemming.
- `prefix='2 3 4'` — automatic prefix indexes for fast prefix queries.
- Every query token is quoted and given the `*` prefix operator, ANDed:
  `"بر"*` etc. FTS grammar characters can never inject the syntax; a
  malformed MATCH falls back to one fully-quoted phrase attempt, then an
  honest empty result.

## Arabic support notes (honest)

unicode61 has **no stemming**: root forms do not match affixed variants
(`سفر` ≠ `تسافر`/`السفر`). Prefix matching (the `*` operator) is the
recall win we ship; aggressive normalization (stripping ال/و/ب) would
corrupt indexed text and is deliberately NOT done. Diacritics tokenize
as separate marks — search the plain form.

## API contract

`GET /api/search?q=<query>&limit=20&offset=0&role=user|assistant&session=<sid>&from=<ts|date>&to=<ts|date>&project=<id>`

Response: `{ok, total, results:[{message_id, session_id, session_title,
role, snippet, highlight, timestamp, score}]}` — `snippet` is plain text;
`highlight` carries `<mark>` tags (rendered as text nodes in the web UI,
never `dangerouslySetInnerHTML`). `score` is BM25 (lower = better).

Role-aware **server-side** (never client-side):

| Caller | Scope |
|---|---|
| Remote non-admin key (tunnel/LAN guest) | **403** |
| Admin (master key / admin account) | all users' messages |
| Issued key / account session | own messages only (`ns` filter) |
| Local mode (no key set) | everything (owner's machine) |

Rate limit: 30 searches/minute per caller (sliding window, 429 beyond).
Errors: 400 empty query, 503 index unavailable.

## Index maintenance

- `_save_session` reindexes the conversation on every save (idempotent
  delete+reinsert per sid); best-effort — indexing can never break a chat.
- Session DELETE removes its rows from the index.
- Boot backfill: every load indexes all conversations once (idempotent);
  plus a lazy per-session backfill on first read of old conversations.
- `python scripts/rebuild_search_index.py [--db PATH]` for manual rebuilds.
- Content-free ops log: `D:/hwk-data/search_index.log` (sids/counts only,
  never message text, never the raw query).

## UI usage

- **Web**: Ctrl/⌘+K or the sidebar 🔍 — live search (200ms debounce),
  ↑/↓ + Enter navigation, role icon + title + highlighted snippet +
  date; Enter/click jumps into the conversation and gold-flashes the
  message (~2.5s). Esc closes. Mobile: full-screen modal.
  Note: Ctrl+K previously focused the composer — now opens search.
- **CLI**: `/search <query>`, `/search --all <query>` (50), 
  `/search --session <sid> <query>`. Last 20 queries persist in
  `~/.aali_cli_search_history` (local only).

## Performance

10k-message index answers in well under 100ms (test-pinned:
`test_performance_10k_rows`). FTS5's bm25 + prefix indexes keep the
working set tiny; WAL lets readers work while the brain writes.
