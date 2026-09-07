---
title: آلي — Aali
emoji: ✦
colorFrom: gray
colorTo: yellow
sdk: docker
app_port: 7860
pinned: true
license: apache-2.0
short_description: مساعدك الذكي المحلي — يبني التطبيقات ويدير الملفات ويتذكر محادثاتك
---

# Aali on Hugging Face Spaces

The Aali server packaged as a Docker Space: open the Space and chat with Aali
in the browser at the Space URL, or call its API from any client:

```
# replace <user>/<space> with the real space name
curl -X POST https://<user>-<space>.hf.space/api/ask \
     -H "Content-Type: application/json" \
     -d '{"message": "مرحبا آلي"}'
```

The web app lives at the Space root (`/ui/` served from web/dist).

## Limits of the free Space tier (verified 2026-09)

- **Sleeps after 48h of inactivity** — first message after a sleep waits
  ~1 minute while the container wakes.
- **Disk is not persistent** — conversations (sessions.jsonl) reset on restart.
  For durable memory, set `AALI_API_KEY` and keep the authoritative copy of
  sessions on an always-on server (Oracle), or add external storage.
- Multi-user: set a Space secret named `AALI_API_KEY` (Settings → Variables
  and secrets) and every client sends it as `X-API-Key`.
