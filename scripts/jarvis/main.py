"""Entry point: wire the config together and run the polling loop.

Usage (on the Pi)::

    python -m jarvis.main                 # run forever (systemd does this)
    python -m jarvis.main --selfcheck     # config + tools + network, no polling
    python -m jarvis.main --once "status"  # run one command and exit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # allow `python main.py` as well as -m
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jarvis import config as _config  # type: ignore
    from jarvis import groq as _groq  # type: ignore
    from jarvis import power as _power  # type: ignore
    from jarvis import tts as _tts  # type: ignore
    from jarvis.commands import Jarvis as _Jarvis  # type: ignore
    from jarvis.log import log_event as _log_event  # type: ignore
    from jarvis.machines import load_machines as _load_machines  # type: ignore
    from jarvis.telegram import TelegramClient as _TelegramClient  # type: ignore
else:
    from . import config, groq, power, tts
    from .commands import Jarvis
    from .log import log_event
    from .machines import load_machines
    from .telegram import TelegramClient


def build(verbose: bool = False, wait_for_token: bool = True):
    """Assemble a ready-to-use Jarvis.

    If ``telegram.json`` is missing and ``wait_for_token`` is set, this WAITS
    for it rather than exiting, so the service can be enabled before the token
    exists and come alive by itself the moment the owner drops the file in.
    """
    cfg_dir = config.ensure_config_dir()
    groq_cfg = config.load_groq(cfg_dir)
    machines = load_machines(cfg_dir / "machines.json")

    tg = None
    warned = False
    while True:
        try:
            tg_cfg = config.load_telegram(cfg_dir)
            tg = TelegramClient(tg_cfg.bot_token, tg_cfg.owner_chat_id)
            break
        except (FileNotFoundError, ValueError) as exc:
            if not wait_for_token:
                raise SystemExit(
                    "telegram.json missing or invalid — put the bot_token in "
                    f"{cfg_dir / 'telegram.json'}"
                ) from exc
            if not warned:
                print(
                    f"[jarvis] waiting for a bot token: {cfg_dir / 'telegram.json'}"
                    " — create it and this starts by itself",
                    flush=True,
                )
                warned = True
            time.sleep(60)

    jarvis = Jarvis(groq_cfg, tg, machines)
    log_event("startup", count=len(machines), model=groq_cfg.chat_model)
    return jarvis


def selfcheck() -> int:
    """Check everything that can be checked without a token."""
    cfg_dir = config.ensure_config_dir()
    print(f"config dir : {cfg_dir}")
    ok = True

    try:
        g = config.load_groq(cfg_dir)
        print(f"groq key   : present ({len(g.api_key)} chars, model {g.chat_model})")
    except Exception as exc:
        print(f"groq key   : MISSING ({exc})")
        ok = False

    machines = {}
    try:
        machines = load_machines(cfg_dir / "machines.json")
        print(f"machines   : {', '.join(sorted(machines))}")
    except Exception as exc:
        print(f"machines   : ERROR ({exc})")
        ok = False

    try:
        tg_cfg = config.load_telegram(cfg_dir)
        print("telegram   : token present (never printed)")
    except Exception as exc:
        print(f"telegram   : MISSING ({exc})")
        ok = False

    print(f"tts tools  : {'ready' if tts.available() else 'MISSING (espeak-ng/ffmpeg)'}")
    print(f"python     : {sys.version.split()[0]}")

    for m in machines.values():
        status = power.machine_status(m)
        api = {True: "open", False: "closed", None: "n/a"}[status["api"]]
        print(
            f"  {m.key:4s} {status['ip']:15s} "
            f"{'online ' if status['online'] else 'offline'} api={api}"
        )

    print("SELFCHECK", "OK" if ok else "INCOMPLETE")
    return 0 if ok else 1


def run(jarvis: Jarvis, voice: bool = True, updates: int | None = None) -> int:
    print("[jarvis] polling for updates (Ctrl-C to stop)")
    handled = 0
    try:
        for update in jarvis.tg.poll_forever(stop_after=updates):
            jarvis.handle_update(update, voice=voice)
            handled += 1
    except KeyboardInterrupt:
        print("\n[jarvis] stopped")
    log_event("shutdown", count=handled)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Aali remote-control bot")
    parser.add_argument("--selfcheck", action="store_true", help="verify config and tools")
    parser.add_argument("--once", metavar="COMMAND", help="run one command then exit")
    parser.add_argument("--no-voice", action="store_true", help="never send voice notes")
    parser.add_argument("--updates", type=int, help="exit after N updates (testing)")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    jarvis = build(verbose=True)

    if args.once:
        jarvis.handle_text(jarvis.tg.owner_chat_id, args.once, voice=not args.no_voice)
        return 0

    return run(jarvis, voice=not args.no_voice, updates=args.updates)


if __name__ == "__main__":
    raise SystemExit(main())