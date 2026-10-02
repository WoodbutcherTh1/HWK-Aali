# C: drive emergency cleanup (100% full)

- owner: buffy (this PC, Freebuff session)
- status: done
- started: 2026-10-01
- done: 2026-10-01

## Result
- Before: 444G/447G used, 2.5 GB free (100%).
- After: 385G/447G used, **62 GB free (87%)**.

## What was done
1. Inventory first (rule): per-drive df + per-dir du probes (AppData dirs,
   caches, temp, user folders, system files). du times out on C:\Windows/
   Program Files (huge, ACL-heavy) — the actionable consumers were all
   found without those numbers.
2. Direct purges (unambiguous, regenerating): user Temp leftovers,
   %LOCALAPPDATA%\CrashDumps (201 MB), Windows Update
   SoftwareDistribution\Download (195 MB) — ~200 MB freed.
3. OWNER-GATED, answered via questions (2026-10-01):
   - **Ollama models 53.6 GB → deleted all EXCEPT glm-low-ram**
     (owner's explicit choice): glm-4.7-flash:q4_K_M/:latest,
     glm-5.3-flash:cloud, gemma4:26b, deepseek-r1:7b, qwen:latest,
     qwen2.5:3b-instruct, llama3.1:8b, qwen2.5:7b-instruct.
     NOTE: glm-4.7-flash shared its 19 GB blob with glm-low-ram (same ID
     46e8c517edca), so it alone freed nothing — recorded for the owner.
     `ollama list` after: only glm-low-ram:latest (19 GB) remains.
   - pagefile.sys (15 GB) + hiberfil.sys (6.8 GB): owner chose KEEP BOTH
     (hibernation-off and pagefile-move-to-D: were offered and declined).
   - Videos (25.4 GB): owner chose leave-alone (personal files; never
     touched).
4. D: has 139 GB free, X: has 319 GB — if C: ever tightens again, the
   known levers are: move pagefile to D:, disable hibernation, move
   OLLAMA_MODELS to D:, review ~/Videos.

## Lessons / notes
- glm-4.7-flash:latest and glm-low-ram:latest were the SAME model under
  two tags (identical ID) — `ollama rm` of one tag deletes nothing until
  all tags sharing the blob are removed.
- du -sh over C:\Windows / Program Files / AppData root reliably exceeds
  any sane timeout on this box; probe subdirs individually instead.
