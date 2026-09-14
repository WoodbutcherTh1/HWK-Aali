"""Phase C chain: after the v4 soup run finishes, SFT Aali's OWN brain.

Aali's from-zero pretrained model exists (Phase B: 100,000 steps / 3.28B
tokens on the pile+arabic corpora with OUR OWN tokenizer, finished 2026-09-12
at D:/hwk-models/context-4k - a 109.6M-param TinyCausalLM). What it never got
is SFT: it completes text, it does not yet answer as Aali. This chain closes
that gap with NO other provider and NO other base model:

  1. Wait for the v4 soup pipeline's done-marker (resft_pipeline_report.md).
  2. Wait for the GPU to actually free (shared wait_gpu_free gate).
  3. train_scratch.py --resume from scripts/bootstrap_sft_state.py's staged
     state (pretrained weights, fresh optimizer, step=0) on the SAME v4 mix
     the soup run just used (media-upweight included - Aali's own brain gets
     the same lever the adapter experiment got).
  4. Smoke-generate from the result so the morning report shows real output.

Runs detached overnight:
    .venv/Scripts/python.exe scripts/launch_detached.py --log phase_c_chain -- \
        .venv/Scripts/python.exe scripts/phase_c_after_v4.py

Log: D:/hwk-data/phase_c_chain.log + D:/hwk-data/phase_c_sft.log (trainer).
The runtime does NOT switch brains automatically: the SFT model lands at
D:/hwk-models/aali-sft-4k/final.pt and a human decides when it replaces
model/scratch/final.pt (the runtime's own-brain path).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

VERDICT = Path("D:/hwk-data/soup/resft_pipeline_report.md")
CHAIN_LOG = Path("D:/hwk-data/phase_c_chain.log")
SFT_LOG = Path("D:/hwk-data/phase_c_sft.log")
SFT_STATE = Path("D:/hwk-models/aali-sft-4k")
SFT_DATA = Path("D:/hwk-data/soup/sft_v2.jsonl")
TOKENIZER = Path("D:/hwk-data/tokenizer/hwk_spm.model")
DONE_MARKER_TEXT = "=== soup pipeline done ==="
# 2026-09-13 lesson: the 8h GPU-wait timed out while the promoted brain held
# the card all day. The wait window is now env-tunable so a relaunch can cover
# verdict + overnight human decision (default unchanged at 8h).
MAX_WAIT_S = int(float(os.getenv("AALI_PHASE_C_MAX_WAIT_H", "8")) * 3600)
POLL_S = 120
# 2026-09-14 run-6 levers (env-tunable): the small-model mix
# (scripts/build_aali_sft_small.py -> sft_small.jsonl) and a step count that
# matches it (~420 optimizer steps/epoch at grad-accum 8; 2500 = ~6 epochs).
SFT_DATA_ENV = os.getenv("AALI_PHASE_C_DATA", str(SFT_DATA))
MAX_STEPS = int(os.getenv("AALI_PHASE_C_STEPS", "2800"))
LEARNING_RATE = os.getenv("AALI_PHASE_C_LR", "2e-5")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def wait_for_v4() -> bool:
    log(f"waiting for the v4 verdict ({VERDICT.name}, poll {POLL_S}s)")
    deadline = time.monotonic() + MAX_WAIT_S
    while not VERDICT.exists():
        if time.monotonic() > deadline:
            log("TIMEOUT waiting for v4 - chain aborted (v4 may still be running)")
            return False
        time.sleep(POLL_S)
    text = VERDICT.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("**Verdict:"):
            log(f"v4 says: {line.strip('*')}")
    # let the pipeline release VRAM / write promoted.json before we grab the card
    time.sleep(120)
    return True


def wait_gpu_free() -> bool:
    import wait_gpu_free as gate  # noqa: E402 - same gate the pipeline uses
    deadline = time.monotonic() + MAX_WAIT_S
    while time.monotonic() < deadline:
        ok, reason = gate.card_is_safe(require_training_log_idle=False)
        if ok:
            log(f"GPU free: {reason}")
            return True
        log(f"waiting (recheck in 5 min): {reason}")
        time.sleep(300)
    log("TIMEOUT waiting for a free GPU - chain aborted")
    return False


def run_sft() -> bool:
    cmd = [
        sys.executable, str(ROOT / "train_scratch.py"),
        "--data", SFT_DATA_ENV,
        "--tokenizer", str(TOKENIZER),
        "--output-dir", str(SFT_STATE),
        "--context", "4096", "--d-model", "768", "--heads", "12", "--layers", "12",
        "--batch-size", "1", "--gradient-accumulation", "8", "--grad-checkpoint",
        "--max-steps", str(MAX_STEPS), "--save-steps", "250", "--log-steps", "10",
        "--learning-rate", LEARNING_RATE, "--warmup-steps", "100",
        "--dtype", "bf16", "--resume",
    ]
    log(f"Phase C SFT starting: {SFT_STATE.name} on {Path(SFT_DATA_ENV).name} "
        f"({MAX_STEPS} steps, lr {LEARNING_RATE}, ctx 4096)")
    with SFT_LOG.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT)
    ok = proc.returncode == 0 and (SFT_STATE / "final.pt").exists()
    log(f"train_scratch exited {proc.returncode} - final.pt present: "
        f"{(SFT_STATE / 'final.pt').exists()}")
    return ok


def smoke_generate() -> str:
    """Load the SFT model and generate answers to two protocol prompts so the
    report shows real behavior, not just a loss curve. Uses the runtime's own
    _scratch_prompt instruction so the sample matches real serving."""
    sys.path.insert(0, str(ROOT / "file-agent"))
    from hwk_model import load_checkpoint  # noqa: E402
    from hwk_model.bpe_tokenizer import load_bpe  # noqa: E402
    from hwk_model.generation import generate_text  # noqa: E402
    import agent_loop  # noqa: E402

    model, _payload = load_checkpoint(SFT_STATE / "final.pt", device="cpu")
    model.eval()
    tokenizer = load_bpe(str(TOKENIZER))
    # Serve-shape prompt: the 2026-09-14 night probe proved a bare
    # prompt+instruction sample MISJUDGES the model - the runtime serves
    # System -> tools -> turns -> instruction, so the smoke must too.
    instruction = (
        "\nReply with either a natural-language answer or exactly one JSON object. "
        'For a tool use {"tool":"tool_name","arguments":{...}}. '
        'For a final JSON answer use {"tool":"final","content":"..."}.'
        "\nAssistant:")
    prompts = [
        "What does quarterly_report_2024.txt say?",
        "سوي لي مقطع لشلال بالموس",
    ]
    lines: list[str] = []
    for prompt in prompts:
        transcript = (
            f"System: {agent_loop.SYSTEM_PROMPT}\n"
            f"Available tools: {agent_loop._scratch_tool_summary()}\n"
            f"User: {prompt}\n"
        )
        try:
            sample = generate_text(model, tokenizer, transcript + instruction,
                                   max_new_tokens=96, temperature=0.3)
        except Exception as exc:
            sample = f"<generation failed: {exc}>"
        lines.append(f"PROMPT: {prompt}\nANSWER: {str(sample)[:400]}")
    return "\n\n".join(lines)


def main() -> int:
    log("=== Phase C chain start: SFT Aali's own brain after v4 ===")
    if not SFT_STATE.exists() or not (SFT_STATE / "checkpoint.pt").exists():
        log(f"FAILED: bootstrapped SFT state missing at {SFT_STATE} - "
            "run scripts/bootstrap_sft_state.py first")
        return 1
    if not wait_for_v4():
        return 2
    if not wait_gpu_free():
        return 2
    if not run_sft():
        log("Phase C SFT FAILED - see D:/hwk-data/phase_c_sft.log "
            "(training_log.csv in the model dir has the loss tail)")
        return 1
    try:
        sample = smoke_generate()
        log("smoke generation:\n" + sample)
    except Exception as exc:  # a generation API drift must not fail the chain
        log(f"smoke generation skipped ({exc})")
    log("=== Phase C chain done - review phase_c_sft.log tail, then decide "
        "whether model/scratch/final.pt gets replaced ===")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
