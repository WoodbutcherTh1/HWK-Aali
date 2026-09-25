# Shared conversations (Wave 3 #8 — 2026-09-25)

Read-only, tokened, expiring links to one conversation — family/friends
can view without keys or accounts. Same credential pattern as the admin
handoff: **the token IS the credential**, stored SHA-256-hashed, shown to
the owner exactly once.

## Model

```
POST /api/shares {sid, title?, ttl_hours?}
  -> {token, url, expires, turns}     # full link ONCE
/share/<token>                         # PUBLIC read-only page
GET  /api/shares                       # my links (content-free)
DELETE /api/shares/<token_hash>        # revoke mine
```

- The page renders a **frozen copy** of the turns at share time (≤100
  turns, ≤20k chars each), Arabic-first RTL, HTML-escaped (test-pinned
  against `<script>` injection).
- **Expiry**: default 7 days, max 30; TTL floor 6 minutes. Revoked or
  expired → the same polite 404 page as unknown tokens (no probing).
- Store: `D:/hwk-data/shares.jsonl`, token hashes only; lazy prune keeps
  it under 500 live shares (newest survive). Revoke is instant — the
  frozen copy becomes unreachable everywhere.
- **ns-scoped**: owners list/revoke only their own namespace's shares;
  revoking another ns's hash returns 404 (test-pinned).
- Guests: every `/api/shares*` route is behind `_require_role` → uniform
  404. The public page itself is reachable only with a valid token, and
  the key gate exempts exactly `/share/` (nothing else).
- Audited content-free: `share_create` / `share_revoke` (actor + ip,
  never payloads).

## Clients

- **Web**: 🔗 button in the sessions sidebar header → share dialog
  (create with optional title, copy-the-link banner shown once, list
  with view counters and expiry dates, revoke buttons).
- **CLI**: `/share [title]` → link printed once; `/share list` (hashes +
  counters); `/share revoke <hash-prefix>`.

## Tests

`tests/test_shares_prompts.py` (15, shared with #9): token hashed not
stored, views counter, revoke kills access, expiry = 404, ns-scoped
list/revoke, garbage/oversized tokens, endpoint round-trip incl. public
page render + revoke → 404, HTML-escape, guest 404s.
