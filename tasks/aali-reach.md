# Aali Reach — PART 3: read a public link, honestly

owner: this-PC (Buffy)
status: in-progress
started: 2026-10-03

## What the owner asked for

> PART 3 — Aali Reach: read-only social link reading via Playwright + Camoufox.

Arabic-first product: the owner pastes a link (a post, an article, a thread) and
Aali reads it, so he does not have to open a dozen apps. **Read-only. Never
posts, never likes, never follows, never logs in.**

## The honest shape of the risk

This is the most dangerous tool in the repo so far, and it is dangerous
because of what it sits NEXT to: an agent that also owns `run_command` on the
owner's machine. A URL fetcher is an SSRF machine and an exfiltration channel
unless it is fenced. So the fencing is the feature, not the fetching:

1. **SSRF** — scheme is http/https only; no credentials in the URL; the host is
   resolved and EVERY resolved A/AAAA record must be public (no loopback, no
   RFC1918, no link-local, no CGNAT, no multicast, no reserved). Re-checked on
   every redirect hop, because a public host can redirect to 127.0.0.1.
2. **No ambient authority** — a fresh throwaway browser context per fetch, no
   persistent profile, no cookies, no storage, and the owner's master key /
   model keys are never attached to the request.
3. **Read-only at the wire** — only GET and HEAD are ever issued; the browser
   bridge blocks every other method plus `javascript:`/`data:`/`file:`
   navigation at the route layer, so page JS cannot turn a read into a write.
4. **Bounded** — response bytes, extracted text, redirects and wall-clock time
   all have hard caps, and every one of them is reported in the result.
5. **Content-free audit** — host, status, byte count and a text hash. Never the
   fetched text, never the URL path, never a query string.

Residual risk, stated plainly: a hostile page still runs JavaScript inside an
isolated browser that holds no credentials and no filesystem access. That is
inherent to reading a rendered page; the alternative is not rendering.

## Layering (why Playwright never enters the training venv)

- `file-agent/file_agent/reach.py` — **stdlib only**: URL validation, SSRF
  fence, the static fetch, the HTML→text extractor, caps, audit. This is the
  part the agent and the tests use, and it works with zero installs.
- `scripts/reach_fetch.py` — the browser bridge. Runs inside its own venv
  (`D:/hwk-tools/reach-venv`) with Playwright + Camoufox, speaks JSON on
  stdout. `reach.py` calls it as a subprocess with the same `launch_detached`
  discipline the repo already uses, and an honest error when it is absent.

AGENTS.md is explicit: no new heavy deps in the training venv. Playwright drags
in a browser; it never touches `.venv`.

## Stages

- [x] **Stage 1** — `reach.py`: validation + SSRF fence + static fetch +
  extractor + caps + audit. Zero dependencies, works today.
- [x] **Stage 2** — `scripts/reach_fetch.py` browser bridge + setup script
  (`D:/hwk-tools/reach-venv`), with the read-only route policy.
- [x] **Stage 3** — the `read_link` agent tool: registry entry, definition that
  teaches WHEN to use it, guest gating, and a teaching mix so the 1.5B student
  learns the name (the toolbelt lesson: invented tool names cost 13/39 exam
  cases).
- [x] **Stage 4** — tests, docs, AGENTS.md.

**Status: DONE (2026-10-03), with two honest gaps:** not yet measured against a
real social site, and camoufox's value unproven. The browser venv is NOT
installed yet — `read_link` works today on `engine=static` and says so
honestly instead of pretending to have rendered anything.

---

## Stage 5 — the live runs, and what they actually proved

The unit suite was green from the start. Three LIVE asks against the served
brain told the truth the suite could not.

### 5.1 The tool was never the bottleneck

`read_link` on `https://example.com` returned the title and the real text. The
brain, handed that text inside its own prompt, answered:

> Sure, let me help you with that! Please share the content of the webpage
> you'd like me to check.

It asked for material it was already holding. This is the toolbelt ceiling the
repo has measured four times (13/39 exam cases): the 1.5B student does not
emit tool calls. So PART 3 answers the way the repo already answers identity
and capability — deterministically. `_link_context` reads the pasted link
BEFORE the model answers, and `_link_fallback` replaces a reply that ignored
the content with an honest EXTRACTIVE digest: the page's real opening lines,
labelled `اقتباس حرفي` (verbatim quote), never a generated summary.

### 5.2 A tuple return shipped a 500 with a green suite

`_link_context` grew a second return value so the rescue layer could see the
read record. Three of its early returns kept returning the bare string, so
unpacking raised `ValueError` inside the request handler — every ask carrying
a link died with a **500 in four milliseconds**. The suite stayed green
because the tests all went through a helper that tolerated both shapes. That
tolerant helper was the bug's shelter; it now asserts the exact tuple, and
three new tests pin every exit (`AALI_REACH_OFF`, no-link, refused-link,
import-failure). LESSON: a helper that accepts "either shape" hides a shape
mismatch until production.

### 5.3 Two more model failure shapes needed their own vetoes

Live replies, in order: an ask-back, then `اذهب إلى الرابط https://example.com
لتحليله.` — a DEFERRAL, handing the owner's own request back to the owner
(`go to the link yourself`, `you can visit it`). Same failure, so it earns the
same veto.

### 5.4 The rescue must never overwrite a good answer

My own test caught the dangerous bug: a **cross-language** paraphrase of an
English page shares no words with it, so pure overlap scoring would have
replaced a perfectly good Arabic answer with the raw excerpt. Overlap is
evidence, not proof — the contract now also accepts a substantial reply as
evidence, and function words are stripped so "the/this/and" can never fake a
read.

### 5.5 The flakiness was MY harness, and that is worth recording

Several Arabic live asks "failed" with no audit entry at all — the link was
never read. Cause: `curl -d "{\"message\":\"اقرأ…\"}"` through the Windows
console mangled the Arabic before it left the shell, so the intent regex never
matched. Sent as a UTF-8 file (`--data-binary @req.json`) the SAME ask
answered `محتوى الرابط:` and quoted the real page text. No product bug; a
measurement bug. Two of the four "failures" chased tonight were mine.

## Honest verdict after the live runs

- Reading works today on `engine=static`, verified live in Arabic and English.
- The model often does NOT use the content it is handed; the deterministic
  digest covers that case, and it is extractive on purpose.
- The browser route (`playwright`/`camoufox`) is BUILT AND UNTESTED — the venv
  is not installed. Do not claim a JS-rendered page has been read.
- Still unmeasured against a real social site (the owner's actual use case).

## Decisions to record as they happen

- **(2026-10-03)** Static-first, not browser-first. Most links are readable
  without a browser; a 400 MB browser install is not a reasonable price for
  the majority of reads. The browser is an escalation, not the default.
- **(2026-10-03)** Camoufox is optional even inside the bridge: Playwright's
  bundled Chromium is tried first and Camoufox is the anti-detect escalation
  for hosts that serve a challenge page. `AALI_REACH_ENGINE` chooses.
- **(2026-10-03)** og:title beats `<title>`. The publisher's own canonical
  title for the CONTENT, while `<title>` is usually the same string plus a
  site suffix.
- **(2026-10-03)** The audit records the HOST only, never the path: a URL's
  query string is where share tokens live, so the path is content too.
- **(2026-10-03)** Stage 3's second half — SFT teaching episodes for the mix —
  is NOT done. The toolbelt lesson (the 1.5B student inventing tool names)
  says it should be, but a mix rebuild is a 5,500-row gated pipeline and the
  owner's go is the right gate for it, not an all-night agent.
