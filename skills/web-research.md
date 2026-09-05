---
name: web-research
description: Looking something up online — current info, documentation, prices, news — using web_search and fetch_url. No API key involved.
---

## When to use this

Whenever the user asks something you can't answer confidently from your own training, asks you to "look up" or "check" something, or gives you a URL directly.

## How

1. `web_search(query)` — returns a short list of `{title, url, snippet}`. Keyless (no API key needed), so it's always available.
2. Pick the most relevant 1-3 results and call `fetch_url(url)` on each to get the actual page text (HTML tags stripped, capped in size). Don't answer from the snippet alone if the user needs specifics — the snippet is a teaser, not the source.
3. Synthesize an answer from what you actually fetched. Never present a search snippet or a guess as if it were the page's content.
4. If `fetch_url` fails (site blocks it, page is behind a login, etc.) say so and try a different result instead of fabricating.

## Boundaries

- `fetch_url` refuses local/private network addresses (127.*, 10.*, 192.168.*, 169.254.*, 172.16-31.*) — that's a safety guard, not a bug; don't try to route around it.
- Both tools are capped in size/time so one bad page can't hang the conversation — if a fetch comes back truncated, say so rather than presenting a partial page as complete.
- Never enter credentials, complete a login, or submit a form through these tools — they're read-only fetchers.
