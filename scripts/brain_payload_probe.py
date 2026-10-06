"""Measure the REAL prompt the openai-compat loop sends, and the model's latency.

The 90s read timeout in _openai_compat_loop is a hard-coded constant. This
script builds the exact payload (SYSTEM_PROMPT + every tool definition + the
user turn) so the prefill cost can be measured instead of guessed at.

Usage: .venv/Scripts/python.exe scripts/brain_payload_probe.py [port] [model]
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "file-agent"))

PORT = sys.argv[1] if len(sys.argv) > 1 else "20129"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "Qwen2.5-3B-Instruct"

import requests
from agent_loop import SYSTEM_PROMPT, get_tool_definitions


def main() -> int:
    tools = get_tool_definitions()
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "How much free disk space is on the C drive?"},
        ],
        "tools": tools,
        "tool_choice": "auto",
    }
    body = json.dumps(payload)
    print(f"tools={len(tools)}  prompt_chars={len(body)}", flush=True)
    for attempt in (1, 2):
        started = time.time()
        try:
            response = requests.post(
                f"http://127.0.0.1:{PORT}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                data=body, timeout=600)
            elapsed = time.time() - started
            print(f"attempt{attempt}: http={response.status_code} {elapsed:.1f}s", flush=True)
            if response.ok:
                message = response.json()["choices"][0]["message"]
                calls = message.get("tool_calls") or []
                print("  tool_calls:", [c["function"]["name"] for c in calls] or "NONE", flush=True)
                print("  content:", str(message.get("content"))[:200], flush=True)
                usage = response.json().get("usage", {})
                print("  usage:", usage, flush=True)
                return 0
        except Exception as exc:  # noqa: BLE001 - timing the failure is the point
            print(f"attempt{attempt}: {type(exc).__name__} after "
                  f"{time.time() - started:.1f}s", flush=True)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())