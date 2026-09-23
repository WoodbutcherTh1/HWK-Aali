# Agent toolbelt + Phase E/F + live jobs board (2026-09-23 afternoon)

- owner: buffy (this PC)
- status: done
- started: 2026-09-23

## Owner requests covered
1. "Check what Claude/DeepSeek/GLM/GPT have that Aali doesn't — give him
   those tools and teach him how to use them right."
2. "Get Phase D done, start Phase E/F, more context, more code/law/vibe-coding
   data."
3. "CLI + desktop: live view of which commands/jobs are running, like the
   Freebuff screen."

## Delivered
### Toolbelt (commit 6880701)
- file_agent/agent_tools.py: create_artifact (RTL HTML/Markdown),
  write_excel/read_excel (openpyxl), create_plot (pure-Pillow bar/line/pie),
  diff_files, todo_plan (.aali-plan.json), recall_search (query-focused
  page reading), screenshot (PowerShell CopyFromScreen).
- Registry 26 -> 34. screenshot: CONFIRM_REQUIRED + _GUEST_BLOCKED_TOOLS.
- Teaching surfaces: every definition carries "RIGHT way" guidance;
  SYSTEM_PROMPT toolbelt paragraph; EN+AR capability answers list the new
  powers; skills/build-apps.md deliverables section.
- Tests: tests/test_agent_toolbelt.py (24; screenshot never executes,
  recall fetcher injected).

### Phase E/F (commit 1097b6a)
- Phase E corpora: code2 = Magicoder-Evol-Instruct-110K (jsonl),
  code3 = CodeFeedback-Filtered-Instruction (jsonl), law2 = pile-of-law
  chunks 2-4 + EURLEX (3.15 GB landed). Downloads verified alive +
  schema-checked BEFORE entering CORPORA (nickrosh repo 401 = deleted).
- tokenize_corpus: plain .jsonl/.json parsing + instruction records
  rendered as Q/A; REFUSES empty manifests (a 0-shard manifest created by
  racing the downloader was deleted - it would have poisoned stage 5).
- Watchdog stage 5: phase-d-all -> phase-d-8k at CONTEXT 8192 (RoPE exempt
  from resume check; flash attention = linear activations; fits 8GB).
  Waits for Phase E only while its pipeline is alive (never deadlocks);
  downloader-alive guard keeps tokenizers off half-written parquet.

### Jobs board (later commit)
- scripts/aali_jobs.py shared snapshot (read-only; skips phase_b's
  pace-state write). CLI /jobs (colored, TAB-completed, help-listed);
  desktop gold pill "شغل حي" with the same rows (Bridge.jobs()).

## Night chain (in motion)
1. Stage 4 finishes ~14:45 (eval 2.08 at step 2300, watchdog relaunches on
   any death).
2. Watchdog advances to stage 5 (ctx 8192) after Phase E tokenization
   completes (tokenizer relaunches automatically; the poisoned-manifest
   fix + downloader-alive guard protect it).
3. After stage 5: card frees -> human decision on Phase C SFT retry
   (recipe fixed + test-pinned) using the wider 8k base.

## Rules honored
- No new deps (Pillow/openpyxl already in the training venv; matplotlib
  deliberately NOT added).
- Screenshot treated as privacy surface (confirm + guest-block).
- PID-verified kills only; detached launches; claim before work; no push.
