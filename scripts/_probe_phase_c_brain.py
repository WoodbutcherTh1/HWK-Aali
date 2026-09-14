"""Throwaway diagnostic (Buffy night 2026-09-14): probe Phase C SFT vs raw
Phase B with the runtime's EXACT serving prompt (System + tools + User +
instruction), the format _local_model_loop actually serves - the chain's
smoke generation used a bare prompt and got degenerate ':::::' output.

Run detached, output redirected:
    .venv/Scripts/python.exe scripts/launch_detached.py --log phase_c_probe -- \\
        .venv/Scripts/python.exe scripts/_probe_phase_c_brain.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402
from hwk_model import load_checkpoint, load_bpe  # noqa: E402
from hwk_model.generation import generate_text  # noqa: E402
import agent_loop  # noqa: E402

SFT = "D:/hwk-models/aali-sft-4k/final.pt"
RAW = "D:/hwk-models/context-4k/final.pt"
TOK = "D:/hwk-data/tokenizer/hwk_spm.model"

PROMPTS = [
    "What does quarterly_report_2024.txt say?",
    "hi, what can you do?",
    "سوي لي مقطع لشلال بالموس",
]


def probe(path: str, label: str, tokenizer) -> None:
    model, _ck = load_checkpoint(path, device="cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    for message in PROMPTS:
        transcript = (
            f"System: {agent_loop.SYSTEM_PROMPT}\n"
            f"Available tools: {agent_loop._scratch_tool_summary()}\n"
            f"User: {message}\n"
        )
        prompt = transcript + (
            "\nReply with either a natural-language answer or exactly one JSON object. "
            'For a tool use {"tool":"tool_name","arguments":{...}}. '
            'For a final JSON answer use {"tool":"final","content":"..."}.'
            "\nAssistant:"
        )
        try:
            out = generate_text(model, tokenizer, prompt,
                                max_new_tokens=120, temperature=0.3)
        except Exception as exc:
            out = f"<generation failed: {exc}>"
        print(f"=== {label} :: {message[:60]}", flush=True)
        print(str(out)[:400].replace("\n", " | "), flush=True)
        print(flush=True)


def main() -> None:
    tokenizer = load_bpe(TOK)
    probe(SFT, "SFT", tokenizer)
    probe(RAW, "RAW-B", tokenizer)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
