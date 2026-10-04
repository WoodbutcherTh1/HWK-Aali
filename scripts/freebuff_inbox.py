"""Read (or fill) the Freebuff handoff inbox on the Pi — the PC side of the bridge.

Telegram → Jarvis runs on the Pi; Freebuff (this agent) runs on the PC. Nothing
can push into a live Freebuff session, so the bridge is a mailbox, and this is
the postman: it opens the inbox over SSH and prints what the owner sent.

    python scripts/freebuff_inbox.py              # peek at unread notes
    python scripts/freebuff_inbox.py --take       # read and mark them delivered
    python scripts/freebuff_inbox.py --send "hi"  # queue a note (bridge test)
    python scripts/freebuff_inbox.py --answers    # only what Freebuff wrote back
    python scripts/freebuff_inbox.py --reply-to ID --text "done"  # answer back
    python scripts/freebuff_inbox.py --json       # machine-readable

The SSH material is discovered, never hardcoded: ``AALI_PI_SSH_KEY`` wins, then
the repo's Pi-CI key under :func:`hwk_paths.data_root`, and the host/user fall
back to the owner's documented values with ``AALI_PI_HOST`` / ``AALI_PI_USER``
overrides. The remote python is whatever the Pi's own venv has — it never
installs or upgrades anything.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "file-agent") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "file-agent"))

DEFAULT_HOST = "192.168.1.15"
DEFAULT_USER = "aalici"
# The SAME interpreter the systemd unit runs — see deploy/jarvis-user.service.
# Guessing a path here would work on the PC and fail on the Pi.
REMOTE_PY = "~/jarvis/.venv-jarvis/bin/python"
# The unit runs with WorkingDirectory=%h/jarvis, and the package lives in
# ~/jarvis/jarvis — so the module is only importable from that directory.
REMOTE_CWD = "~/jarvis"
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10")


def ssh_key() -> Path | None:
    """Locate the Pi SSH key, or ``None`` to let ssh use the agent/default."""
    override = os.environ.get("AALI_PI_SSH_KEY")
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    try:
        from file_agent import hwk_paths  # type: ignore

        candidates = [
            hwk_paths.data_root() / "pi_ci" / "pc_to_pi_ed25519",
            hwk_paths.data_root() / "pi_ci" / "id_ed25519",
        ]
    except Exception:
        candidates = [Path("D:/hwk-data/pi_ci/pc_to_pi_ed25519")]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def remote_argv(args: argparse.Namespace) -> list[str]:
    """Build the remote shell command for the handoff module.

    Only the two values that can contain anything at all -- a config dir and
    the note text -- are quoted. The interpreter path must stay UNQUOTED so the
    remote shell expands the ``~``: quoting it produced
    ``~/jarvis/.venv-jarvis/bin/python: No such file or directory`` on the Pi.
    """
    parts = ["cd", REMOTE_CWD, "&&", REMOTE_PY, "-m", "jarvis.handoff"]
    if args.config_dir:
        parts += ["--config-dir", shlex.quote(str(args.config_dir))]
    if args.take:
        parts.append("--take")
    if args.all:
        parts.append("--all")
    if args.answers:
        parts.append("--answers")
    if args.json:
        parts.append("--json")
    if args.limit:
        parts += ["--limit", str(int(args.limit))]
    if args.reply_to:
        # The answer path. --text is quoted for the same reason --send is: it is
        # the owner's or Freebuff's own words and may contain anything at all.
        parts += ["--answer", "--reply-to", shlex.quote(str(args.reply_to))]
        if args.text is not None:
            parts += ["--text", shlex.quote(str(args.text))]
        elif args.send is None:
            # An answer with no text would silently write nothing; refuse here
            # rather than pretend a reply was delivered.
            raise SystemExit("--reply-to needs --text (or --send) with the answer")
    elif args.send is not None:
        parts += ["--send", shlex.quote(str(args.send))]
    elif args.text is not None:
        parts += ["--send", shlex.quote(str(args.text))]
    if args.path:
        parts.append("--path")
    return parts


def run(args: argparse.Namespace, runner=subprocess.run) -> int:
    host = args.host or os.environ.get("AALI_PI_HOST", DEFAULT_HOST)
    user = args.user or os.environ.get("AALI_PI_USER", DEFAULT_USER)
    key = ssh_key()
    remote = " ".join(remote_argv(args))
    argv = ["ssh", "-i", str(key), *SSH_OPTS, f"{user}@{host}", remote]
    if key is None:
        # -i cannot be empty; drop it and let the agent/default key win.
        argv = ["ssh", *SSH_OPTS, f"{user}@{host}", remote]
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        print("ssh is not available on this machine", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print(f"the Pi ({host}) did not answer in 90s", file=sys.stderr)
        return 2
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="freebuff_inbox", description="Pull the Freebuff handoff inbox from the Pi"
    )
    parser.add_argument("--host", default=None, help=f"Pi IP (default {DEFAULT_HOST})")
    parser.add_argument("--user", default=None, help=f"Pi user (default {DEFAULT_USER})")
    parser.add_argument("--limit", type=int, default=20, help="how many notes to pull")
    parser.add_argument("--take", action="store_true", help="mark the notes as delivered")
    parser.add_argument("--all", action="store_true", help="include already-read notes")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--send", default=None, metavar="TEXT", help="queue a note (bridge test)")
    parser.add_argument(
        "--text", default=None, metavar="TEXT", help="the body of --reply-to"
    )
    parser.add_argument(
        "--reply-to",
        default=None,
        metavar="ID",
        help="answer one of the owner's notes (Freebuff -> owner)",
    )
    parser.add_argument(
        "--answers", action="store_true", help="only what Freebuff wrote back"
    )
    parser.add_argument("--path", action="store_true", help="print the remote inbox path")
    parser.add_argument("--config-dir", default=None, help="remote Jarvis config dir")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
