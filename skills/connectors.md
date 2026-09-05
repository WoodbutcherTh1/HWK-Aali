---
name: connectors
description: How Aali's cloud mode works with multiple LLM providers (OpenRouter, OpenAI, Anthropic, Gemini) — what "connecting a provider" means and why keys never go through chat.
---

## The model

Aali runs local-first by default (Ollama on this machine, or the in-training scratch checkpoint). "Cloud mode" (`mode: "cloud"`) is an optional escape hatch for when a bigger hosted model is useful, and it supports four connectors:

| provider     | env var              | note                                   |
|--------------|-----------------------|-----------------------------------------|
| `openrouter` | `OPENROUTER_API_KEY`  | default cloud path if no provider given |
| `openai`     | `OPENAI_API_KEY`      | direct OpenAI, same schema as OpenRouter |
| `anthropic`  | `ANTHROPIC_API_KEY`   | native Messages API + tool-use loop     |
| `gemini`     | `GEMINI_API_KEY`      | native generateContent + function-calling loop |

## The one rule that matters

A key is never typed into the chat, and Aali never asks for one there. The user sets the environment variable on their own machine (before starting the app) and picks a provider from the UI's connector picker or the `provider` field on a request. If a request asks for a provider whose env var isn't set, the honest answer is "no key configured for X — set `<ENV_VAR>` and restart", not a workaround.

## When a user says "connect Claude / GPT / Gemini"

That means: help them set the right environment variable on their machine (tell them the exact variable name from the table above) and pick that provider in the UI — never ask them to paste the key into the conversation, and never store a key you're told in a file or commit.

Every connector — local Ollama, the scratch model, or any of the four cloud providers — runs through the exact same tool loop (file tools, skills, web tools, the confirmation policy). Switching providers changes which model answers, never what it's allowed to do.
