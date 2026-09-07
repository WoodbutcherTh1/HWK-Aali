# Aali's long-term memory — how the 07:00 → 19:00 promise works

*Added 2026-09-06 at the owner's request: "if he asks Aali at 07:00 not to do a
thing and returns at 19:00, Aali should remember — and if he then says yes,
Aali should tell him he earlier said no."*

## What was built

| Piece | File | What it does |
|---|---|---|
| Memory engine | `file-agent/file_agent/memory.py` | Store/recall/forget user statements with timestamps; surfaces contradictions; redacts secrets; never stores Aali's own beliefs |
| `memory` tool | `file-agent/file_agent/file_tools.py` | Aali calls `memory(action: save/recall/forget/summary, ...)` like any other tool |
| Prompt injection | `file-agent/agent_loop.py` | Every system prompt (Ollama, scratch, cloud, native providers) now includes a "Long-term memory about this user" block (newest first, max 25 entries) |
| Auto-recording | `agent_loop()` | Every user message and Aali's final reply are appended to `~/.aali/aali_conversations.jsonl`; directive phrases ("من الآن…", "always/never…", "remember that…") are auto-saved to memory even if Aali forgets to call the tool |
| Forget gating | `_is_dangerous_call` | `memory forget` requires explicit user confirmation under the `always_ask` policy (blocks "forget everything I told you" injection attacks) |
| Ollama context | `_ollama_chat` | `num_ctx` raised to 8192 (env `AALI_OLLAMA_NUM_CTX`) so the memory block + long chats fit |

## Where memory lives

- `%USERPROFILE%\.aali\aali_memory.json` — the structured memory store
  (atomic writes; survives restarts, new conversations, and app updates).
- `%USERPROFILE%\.aali\aali_conversations.jsonl` — raw timestamped turns,
  later convertible to SFT episodes via `scripts/teacher_to_sft.py`.
- Both can be redirected with the `AALI_MEMORY_DIR` env var (tests use this).
- Neither is inside the repo; nothing is ever sent anywhere by the memory
  system itself — only the rendered block travels inside system prompts.

## The guarantees

1. **Persistence** — entries are on disk before the reply is sent; a new
   conversation at 19:00 sees what was said at 07:00.
2. **Newest instruction wins** — the memory block is sorted newest-first and
   the prompt says to follow the newest entry per topic.
3. **Contradictions surface** — when a new statement conflicts with an older
   one on the same topic+kind, `remember()` returns the older entry so Aali
   can say "سابقًا طلبت X" instead of silently forgetting or silently obeying.
4. **Only the user writes memory** — `source != "user"` raises; tool results,
   web pages, and files can never persist instructions (anti memory-poisoning).
5. **Secrets never enter memory** — credential-like text is redacted to
   `[REDACTED-SECRET]` before storage (Samsung/DeepSeek lesson), so the block
   that rides inside system prompts to cloud providers is safe.
6. **Recall before denial** — prompts instruct Aali to call
   `memory(recall)` *before* ever saying "I don't know what you told me".

## Verified behaviour (2026-09-06, 31/31 tests green)

- Live demo: 07:00-style "never open WhatsApp links" → still rendered in a
  fresh conversation hours later → owner reverses → contradiction returned:
  `earlier you told me (2026-09-06 18:27): never open WhatsApp links...`
- Exam: 8 new cases (memory save/recall AR+EN, contradiction surfacing,
  env-dump refusal, prompt-extraction refusal, memory-poisoning refusal)
  bring the promotion gate to **24 cases** (re-exported for Soup comparison).
