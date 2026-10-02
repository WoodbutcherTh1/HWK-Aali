# -*- coding: utf-8 -*-
"""Run the voice server from scripts/voice/config.yaml.

  python scripts/voice/server.py              # WS :5080 + UI :5081 (+ studio API separate)
  python scripts/voice/server.py --check      # config + engine report, no servers
  python scripts/voice/server.py --fake       # deterministic seams, no models

`docs/features/voice_agent.md` pointed at this file since Phase 1, but it
never existed — the server could only be started from a REPL. This is the
documented entrypoint it should have had.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_REPO_ROOT, "file-agent")):
    sys.path.insert(0, os.path.join(_REPO_ROOT, "file-agent"))

from voice import force_utf8_stdio  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_cfg(path: Path = CONFIG_PATH) -> dict:
    import yaml

    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main(argv: list | None = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="voice-server")
    ap.add_argument("--ws-port", type=int, default=None)
    ap.add_argument("--http-port", type=int, default=None)
    ap.add_argument("--fake", action="store_true", help="no models, wiring check")
    ap.add_argument("--check", action="store_true",
                    help="print the config + engine report and exit")
    args = ap.parse_args(argv)

    from voice.core.server import VoiceServer, build_pipeline

    cfg = load_cfg()
    if args.check:
        engines = {}
        try:
            engines = build_pipeline(cfg, fake=args.fake).tts.engine_report()
        except Exception as exc:  # honest: report WHY it cannot build
            engines = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"config: {CONFIG_PATH}")
        print(f"  llm        : {cfg.get('llm', {}).get('base_url')} "
              f"(key from AALI_VOICE_API_KEY / AALI_API_KEY)")
        print(f"  wake phrase : {cfg.get('wake_word', {}).get('phrase') or '(always answer)'}")
        print(f"  partial STT : {cfg.get('partial_stt', {}).get('interval_s', 0)}s")
        print(f"  engines     : {engines}")
        return 0

    pipeline = build_pipeline(cfg, fake=args.fake)
    server = VoiceServer(
        pipeline,
        ws_port=args.ws_port or int(cfg.get("server", {}).get("ws_port", 5080)),
        http_port=args.http_port or int(cfg.get("server", {}).get("http_port", 5081)),
        partial_interval_s=float(cfg.get("partial_stt", {}).get("interval_s", 0) or 0),
    )
    print(f"[voice] wake phrase: {pipeline.wake_phrase or '(always answer)'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[voice] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())