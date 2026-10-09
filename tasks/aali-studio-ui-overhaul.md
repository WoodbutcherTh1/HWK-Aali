# Aali Studio — UI/UX Overhaul Plan (DESIGN FIRST, no code yet)

owner: Buffy (Codebuff) · status: awaiting-owner-review · started: 2026-10-09

> الطلب: «ok for now u work on the ui & ux, we need that like deepseek site or
> gemini» — the owner picked «UI/UX overhaul plan» for Studio (mac/windows).
> This file is the PLAN. No implementation until he reviews it.

## 0. The one design decision

Studio's current face is **Austere IDE** (warm-black #1b1b19, gold #d8b46a,
radius 10, emoji in buttons, dense chrome). The web client already shipped
the **Cinematic Chat** face (glass panels, radius 16, ambient gold glow,
dark+sepia themes, polished empty-states). The overhaul = **one design
language on both**: Studio adopts the web client's surfaces, tokens and
calm, without losing the IDE's density where density is right (editor,
terminal, tree).

**North star**: DeepSeek/Gemini = walk in, the CHAT is the product, the rest
is quiet chrome. Studio = walk in, the AGENT PANEL is the product (it is
already the right pane), the editor/tree are quiet chrome around it.

## 1. Design tokens (single source)

New `tokens.css` shared verbatim by Studio (`build-desktop/aali-studio/web/`)
and ported into `web/src/styles.css`:

| token | Studio now | becomes (from web client) |
|---|---|---|
| --bg | #1b1b19 | #252523 (warm near-black) |
| --bg-2/-3 | #232321/#2b2b28 | #2b2a28/#34302d |
| --gold | #d8b46a | #e8b34b (+ --gold-dim glow) |
| --radius | 10px | 14px chrome / 8px editor-toolbars (IDE density stays tighter than chat) |
| surfaces | flat fills | glass: `backdrop-filter: blur(12px) saturate(1.1)` + inset hairline, ONLY on topbar/agent-panel/composer — never on editor/terminal |
| themes | dark only | dark + **sepia** (`:root[data-theme=sepia]`, same token map as web) — same keys, same localStorage habit |

Rule: tokens.css defines ONLY variables + the two theme maps. Every component
keeps its own rules referencing the vars. A test pins that style.css contains
no literal hex outside tokens.css (except the fallbacks already in var()).

## 2. The three panes, re-ordered by what the product IS

Current: tree | editor | agent — equal visual weight, emoji buttons,
5 different pane-heads. Overhaul:

1. **Agent panel first (visually)**: slightly wider default (min 380px),
   glass surface, the composer grows the web client's treatment
   (soft gold focus ring, chips as gold pills, send = round gold button).
   Thinking state gets the web client's **ThinkingOrbit** instead of the
   💭 label alone (the 💭 tail stays as the hover expand).
2. **Editor is the workspace**: token-mapped only — Monaco/terminal/tree
   keep their density. The ONLY structural change: tabs get a quiet
   close-× + dirty-dot on ONE line, and the terminal head merges with the
   tab row (one fewer strip of chrome on short windows).
3. **Tree slims down**: the pathbar + ws-row merge into one row
   (workspace name IS the path label; 📂 opens the switcher). Footer
   buttons become icon+tooltip (💾 ＋ 🗑) — Arabic-first tooltips stay.

## 3. Kill the emoji-in-button problem

Buttons currently read: 💾 حفظ · ▶️ تشغيل · 🔗 قراءة · 🔑 المفاتيح · ℹ️.
On macOS these render colored and clash (the tree-carets lesson, 2026-10-03).
Replaced with **inline SVG glyphs** (one `icons.js`, stroke=currentColor,
14px) + Arabic text labels. Emoji survives ONLY in chat content and toasts.

## 4. Empty states + first run (the DeepSeek moment)

- No file open → editor shows a centered quiet card: «افتح ملفاً من الشجرة،
  أو اكتب Ctrl+P» with the keyboard hints, not a blank void.
- Agent panel, no history → the web client's time-aware Arabic greeting
  (صباح/نهارك/مساء الخير) + 3 example asks (build app / fix error / explain
  file), each a chip that fills the composer (insert-only, never auto-send).
- First run with brain unreachable → the brain-banner is redesigned to a
  card with the exact address tried + the settings button inline.

## 5. Account tier in Studio (ties into the 2026-10-09 guest tier)

Studio talks to :5055 in key mode today via a pasted key in models_proxy.
Plan: the keys dialog gains an «الحساب» section — email+password login
(X-Session-Token) OR the master key, remembered in %APPDATA%/AaliStudio.
A signed-out Studio still works against the guest tier for chat-only asks,
with the same «أنشئ حساباً» upsell CTA as the web client when capped (429).

## 6. Cross-platform checklist (mac/windows — iOS is out, honestly)

- macOS: stop hardcoding Windows-isms in chrome — emoji glyphs (§3 fixes),
  font stack already has Menlo; test the --mac-font-size default; window
  vibrancy stays OFF (glass does the job inside the webview).
- Windows: WebView2 blur support is fine; keep Cascadia Mono.
- All builds go through the existing specs; tokens.css + icons.js are static
  assets — no PyInstaller changes needed.
- The MacBook verification (build_all_macos.sh check + screenshots) remains
  OWNER-MACHINE-ONLY.

## 7. Implementation order (each step independently shippable + tested)

1. tokens.css + theme maps + sepia in Studio (test: token file contract,
   no stray hex).
2. icons.js + button de-emoji across index.html (test: no emoji in button
   labels; SVG icon registry test).
3. Agent panel + composer restyle (glass, orbit, gold pills, empty states).
4. Tree/editor chrome merge + tab row (behavioral test: tab close/dirty
   still work — existing 113 tests stay green).
5. First-run/empty states + example chips.
6. Account section in the keys dialog + 429 upsell reuse.
7. Rebuild exe (build.bat), screenshot both themes, update
   docs/features/aali_studio.md.

Tests live in tests/test_aali_studio.py (currently 113 — the suite must
STAY green; new UI contracts are asserted server-side where possible,
source-tripwires for the de-emoji rule).

## Honest limits

- Studio's face is served from the exe's static assets: existing installs
  see the new face only after auto-update (the updater pipeline already
  handles that).
- No touch redesign: Studio is a desktop IDE; mobile stays the web client.
- iOS/iPad: a native SwiftUI or WKWebView Studio shell is a separate project
  and needs the MacBook — not part of this plan.
