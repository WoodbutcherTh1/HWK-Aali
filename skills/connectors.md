---
name: connectors
description: How Aali's optional remote-brain bridges work — what "connecting a bridge" means and why keys never go through chat.
---

## The model

Aali runs local-first by default (his own trained model when promoted, the
local chat brain, or direct-command mode). Optional bridges (`mode: "cloud"`)
are an escape hatch for when a bigger remote model is useful; every bridge is
bring-your-own-key, configured by env vars on the machine that runs the server.

| bridge        | env var              | note                                   |
|---------------|-----------------------|-----------------------------------------|
| `aali_remote` | `AALI_REMOTE_BRAIN_URL` (+ `_KEY`) | another Aali server acting as THE brain (the Pi hosting setup) |
| managed       | server-side managed key | default cloud path when configured by the owner |
| generic       | provider env vars     | same standard tool loop as every other mode |

## The one rule that matters

A key is never typed into the chat, and Aali never asks for one there. The
owner sets the environment variable on the server machine and picks the bridge
from the connector picker or the `provider` field on a request. If a request
asks for a bridge whose env var isn't set, the honest answer is "no key
configured — set the env var and restart", not a workaround.

## When a user asks to connect an external bridge

That means: help them set the right environment variable on the server machine
(tell them the exact variable name) and pick that bridge in the UI — never ask
them to paste the key into the conversation, and never store a key you're told
in a file or commit.

Every brain — Aali's own model, the local chat brain, direct commands, or any
bridge — runs through the exact same tool loop (file tools, skills, web tools,
the confirmation policy). Switching brains changes which model answers, never
what it's allowed to do.
