# Provider audit — does Aali depend on other AI providers?

**Date:** 2026-09-08
**Policy:** *Aali is the provider.* At runtime Aali serves **his own model** and
issues **his own API keys**; he does not call other AI companies' APIs or use
their keys in the user-facing product. External models are allowed **only as
bootstrapping** to build his training data on this workstation.

This audit walks every code path that talks to an external AI provider and
states how it relates to the policy.

---

## 1. Runtime (what the server actually serves users)

| Path | File | What it calls | Status under policy |
|---|---|---|---|
| Own promoted model | `file-agent/agent_loop.py` → `promoted_own_model()` + `_openai_compat_loop` | Aali's tuned adapter served via `soup serve` (OpenAI-compatible, localhost). | ✅ **This is the product brain.** Own model only. |
| Ollama brain | `file-agent/agent_loop.py` `_ollama_*` | Local Ollama server (`http://127.0.0.1:11434`, model `qwen2.5:7b`). | ⚠️ **Interim only.** Local, not an external provider. Used until the own model is promoted. Disable with `AALI_OLLAMA=0`. |
| Scratch model | `file-agent/agent_loop.py` `_local_model_loop` | The from-scratch `.pt` checkpoint (`LOCAL_MODEL_PATH`). | ✅ Own model. |
| Cloud connectors | `file-agent/providers.py` (Anthropic / OpenAI / Gemini / OpenRouter) | External APIs: `api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com`, `openrouter.ai`. | 🚫 **Not in the product path.** Only reachable via `mode="cloud"` **and** the matching env key set. Default is `mode="local"`. Left for dev/testing on this box; the product must not use them. |
| Replit-managed | `file-agent/agent_loop.py` (`AI_INTEGRATIONS_OPENAI_API_KEY`) | External managed OpenAI-compatible endpoint. | 🚫 Same as above — `mode="cloud"` only. |
| Web tools | `file_agent/web_tools.py` (`web_search`, `fetch_url`) | Public web. | ⚠️ **Not a model provider** — these are user-facing tools (search/read pages). Allowed; they are not AI providers. |

**Verdict:** the user-facing path is **self-contained**. External providers are
behind `mode="cloud"` + explicit env keys, which is a dev convenience, not the
product.

**One leak to fix in code:** `DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"`
in `agent_loop.py` names an external model as the default. It is harmless
today because `mode` defaults to `local`, but it is a misleading default and
should become `aali-own` so the provider posture is unambiguous.

---

## 2. Training (building Aali's data — allowed bootstrapping)

| Path | File | What it calls | Status |
|---|---|---|---|
| Claude teacher | `claude_teach.py` | Claude Desktop app (installed on this PC). | ✅ Bootstrapping. |
| DeepSeek teacher | `deepseek_teacher.py` | `chat.deepseek.com` (WebView2). | ✅ Bootstrapping. |
| OmniRoute mentor lab | `scripts/omniroute_mentor_lab.py`, `scripts/overnight_arena.py` | Claude via local OmniRoute gateway (`localhost:20128`). | ✅ Bootstrapping / evaluation reference. |
| Claude Code route | `claude-free.bat` | Claude Code through local gateway. | ✅ Dev tooling. |
| Mentor capture | `scripts/mentor_capture.py` | Reads `~/.claude/projects/**/*.jsonl` (Claude Code session logs). | ✅ Bootstrapping. |

**Verdict:** training uses external models as teachers to generate SFT data on
this workstation. This matches the policy: *teachers build Aali; Aali's runtime
never calls them.*

---

## 3. Summary

- **Product runtime:** own model only (promoted adapter → scratch). Local
  Ollama is an interim fallback, not an external provider.
- **Cloud connectors / Replit-managed:** exist but are not in the default path;
  they require `mode="cloud"` + an env key. Consider marking them
  `dev-only` and removing them from the shipped image.
- **Web tools:** allowed (not AI providers).
- **Training:** external teachers allowed for data generation only.

**Action items:** (1) change `DEFAULT_MODEL` to `aali-own`; (2) confirm cloud
connectors are not wired into `/api/ask` default path (they are not);
(3) keep `AALI_API_KEY`/issued keys as the only gate on `/v1`.
