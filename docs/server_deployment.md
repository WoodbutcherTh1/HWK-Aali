# Putting Aali on a server — verified options & step-by-step

*Researched 2026-09-07 (free tiers change fast — every claim below was checked
against current sources that day). The owner's goal: Aali lives on a server
and talks with users via web / desktop / CLI / terminal, like every other AI.*

## Which free server fits Aali

| | Oracle Cloud Always Free ⭐ | Hugging Face Space | Render/Koyeb free |
|---|---|---|---|
| Always on | ✅ 24/7 | ❌ sleeps after 48h idle | ❌ sleep / hour caps |
| RAM / CPU | 12 GB, 2 ARM OCPU (halved Jun 2026 from 24/4) | 16 GB, 2 vCPU | 0.5–2 GB |
| Persistent sessions | ✅ full disk | ❌ ephemeral | ❌ ephemeral |
| Runs the scratch brain on CPU | ✅ comfortably | ✅ | ❌ too small |
| Real public URL / domain | ✅ + your own domain | ✅ `*.hf.space` | ✅ |
| Signup needs | **credit/debit card** (identity check) | email only | email |
| Cost | $0 forever (Always Free) | $0 | $0 with limits |

**Recommendation**: Oracle = Aali's real home (always-on, memory persists,
full control, Docker). HF Space = instant public demo window with zero setup.
They compose: the Space can front the world while Oracle holds the state, and
both run the exact same image from this repo.

## Path 1 — Oracle Cloud Always Free (the real server)

1. Sign up at <https://www.oracle.com/cloud/free/> — needs name, email
   (hmam.kaadna@gmail.com), and a credit/debit card for identity verification
   (no charge on Always Free; only you can do the card step).
2. Create a compartment → Compute → Create instance:
   - Shape: **VM.Standard.A1.Flex**, 2 OCPU / 12 GB (the Always Free shape)
   - OS: Ubuntu 22.04/24.04 (aarch64)
   - SSH key: add your public key (or paste one generated on the PC)
   - If "out of capacity": retry other home regions or off-peak times — known issue.
3. Open port 5055: VCN → Security List → Ingress rule, TCP 5055 from 0.0.0.0/0
   (and in the OS: `sudo ufw allow 5055`).
4. Install Docker (Ubuntu: `sudo apt install docker.io docker-compose-v2 -y`
   then `sudo usermod -aG docker $USER`).
5. Clone the repo on the VM, then:
   ```
   echo "AALI_API_KEY=pick-a-long-key" > .env
   docker compose up -d --build
   ```
6. Clients point at `http://<VM-PUBLIC-IP>:5055` (web app at `/ui/`, CLI:
   `python aali_cli.py --server http://<IP>:5055`). Optional hardening later:
   Caddy on :443 with a domain → automatic HTTPS.

Note: if the tenancy was created before Aug 2026 you may still see 4 OCPU/24 GB.
New tenancies get 2/12 — still plenty for Aali's Flask server + CPU brain.

## Path 2 — Hugging Face Space (the instant demo)

1. Sign up at <https://huggingface.co/join> with hmam.kaadna@gmail.com
   (email verification only; you'll receive a code/link — that's the only step
   that needs you).
2. New → Space → SDK: **Docker** → name it `aali` (public or private).
3. Upload `deploy/hf_space/Dockerfile` + `deploy/hf_space/README.md` (the
   README carries the Space metadata: sdk docker, app_port 7860) plus the
   repo contents it copies (`file-agent/`, `web/dist/`).
4. (Optional multi-user) Settings → Variables and secrets → add
   `AALI_API_KEY`.
5. The Space builds and serves the web app at its URL; API at
   `https://<user>-<space>.hf.space/api/ask`.

Space limits (free tier, verified 2026-09): sleeps after 48h idle (~1 min
cold start), ephemeral disk (conversations reset on restart).

## What is already prepared in the repo

- `Dockerfile` + `docker-compose.yml` — the production image (waitress WSGI,
  multi-user env, volumes for workspace/sessions).
- `deploy/hf_space/` — the Space variant (port 7860, non-root user, metadata).
- `docs/desktop_app.md` — the one-server-many-clients architecture.
- Multi-user API: `AALI_API_KEY` → `X-API-Key` gate + per-user session
  isolation (tests 46/46 green).

## Security notes (Aali's own rules apply to me)

- Never put the card, password, or API key in chat. Only verification *codes*
  the user receives come back through the conversation — nothing else.
- Change the auto-generated password after first login (I never need it again).
- Set `AALI_API_KEY` on any public deployment so random visitors can't use
  (or poison) Aali's memory.
