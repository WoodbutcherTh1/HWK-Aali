# Custom assistants (Wave 4 — 2026-09-25)

Named persona configs OVER the existing machinery: an assistant is a
small record (name, icon, tagline, instruction, optional project pin)
the user binds to their session. At ask time the persona instruction is
injected as labeled text — the SAME mechanism as project instructions;
nothing new executes and no policy gate is weakened.

## Security contract

- **Persona is TEXT only** — it can never change the tool policy. The
  client-supplied policy and the server-enforced remote-guest policy
  keep their full precedence; the persona rides inside the message.
- **ns-scoped** like projects/prompts: users see and bind only their
  own (`u<key_id>` / local — same `_projects_ns()` contract); one
  user's assistant can never leak into another's context.
- **Guests have no assistants surface** — uniform 404 (never 403), and
  they are excluded from the web nav + slash menu by the role filter.
- Persona injection happens on `/api/ask` + `/api/ask/stream` (the
  interactive chat paths), AFTER the project-instruction prepend so the
  persona line leads the message the brain sees.

## Store

`D:/hwk-data/assistants.jsonl` — one JSON object per line (same JSONL
pattern as prompts/shares; atomic tmp+replace writes). Record:
`{id: "as_"+hex, ns, name, icon, tagline, instruction, project_id,
created}`. Caps: name ≤40, tagline ≤120, instruction ≤2000,
≤50 per ns. Arabic-first errors everywhere.

## API

```
GET    /api/assistants              -> {ok, assistants:[...]}   (user+)
POST   /api/assistants              {name, icon?, tagline?, instruction?, project_id?} -> 201
PUT    /api/assistants/<aid>        partial update (JSON has no False vs None:
                                    absent project_id = leave, false/"" = unpin,
                                    string = pin)                -> ok | 404 | 400
DELETE /api/assistants/<aid>        delete + detach ALL session binds -> ok | 404
GET    /api/assistants/active?sid=  -> {ok, assistant|null}     (chat chip)
POST   /api/assistants/active       {sid, assistant_id|null}    -> bind/unbind
```

Unknown session → 404; unknown assistant → 404; guests → uniform 404 on
every route. On DELETE the server clears `assistant_id` from every
in-memory session referencing it, and any stale bind (assistant deleted
by another client) detaches silently at the next ask — a session never
speaks with a ghost persona.

## Persona block format

```
[أنت الآن تتصرف كمساعد مخصص باسم «الاسم» — وصفه: … — تعليمات الشخصية: …]
```

Empty string when no assistant is bound (honest empty, never a fake
persona). It composes with project instructions: persona line first,
then `[تعليمات المشروع: …]`, then the user message.

## Clients

- **Web**: 🎭 nav item + `/assistants` slash command → assistants
  dialog (icon picker, name/tagline/instruction form, list with
  مفعّل badge, bind/unbind). Active assistant shows a header chip
  (icon + name, click to unbind), refreshed with the session like the
  project chip.
- **CLI**: `/assistants` (numbered list) → `/assistants new <name>`
  (+ paste ending with a lone `+++`), `use <n>`, `off`, `edit <n>`,
  `del <n>`. TAB-completed; in `/help`.

## Tests

`tests/test_assistants.py` (21): store CRUD + Arabic errors, ns
isolation (get/list/delete), newest-first list, caps + trimming, per-ns
cap, pin/leave/unpin semantics, persona_block (labeled AR text, honest
empty), guest 404 on every route, endpoint round-trip + 404s, bind/
unbind + active endpoint (unknown session/assistant 404), ask-time
injection (persona LEADS, composes after project prepend), stream path,
persona-never-touches-policy, delete detaches all sessions, vanished
assistant detaches silently at ask. Key-format lesson pinned: tests
replicate `u<key>:<sid>` OUTSIDE a request context (never call
`_user_ns` outside one).
