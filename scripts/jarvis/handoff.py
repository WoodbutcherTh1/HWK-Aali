"""The Freebuff inbox: the bridge from Jarvis (Telegram) to Buffy (Freebuff).

Why an inbox and not an API call
--------------------------------
Nothing can inject a message into a live Freebuff chat session from outside
it — there is no such endpoint, by design. So the honest shape of "send this
to Freebuff" is a mailbox: Jarvis drops the owner's words into a file, and
Freebuff reads that file when the owner asks it to. The inbox lives on the Pi
because the Pi is always on and Freebuff already holds its SSH key.

Design rules (from the repo's standing conventions)
---------------------------------------------------
* **The inbox is the ONE place message text is allowed to exist.** Everything
  else in Jarvis is content-free, so the text is confined here, the file is
  mode 600 inside a 700 dir, and :mod:`jarvis.log` still records only sizes.
* **Secrets are scrubbed on the way in.** The owner has already leaked a Groq
  and a DeepSeek key into a chat once. A handoff must not become the place
  that stores the next one, so anything that *looks* like a credential is
  replaced before it is written and the caller is told it happened.
* ** The file is capped.** A bot that appends forever on a 905 MB Pi is a bug
  waiting for the SD card to fill, so the inbox keeps only the newest entries.

Two kinds of note
-----------------
The inbox became two-way once the owner said he wanted to work from outside
the house. A record's ``kind`` says which way it travels:

* ``note``  — owner → Freebuff ("do this on the repo").
* ``answer`` — Freebuff → owner ("done, tests are green").

Jarvis delivers unseen ``answer`` records to Telegram and marks them handled;
he never delivers his own ``note`` records back to the sender, because that is
just an echo of what he just said.

Reading it back from the PC::

    python scripts/freebuff_inbox.py            # peek, pending
    python scripts/freebuff_inbox.py --take     # read and mark handled

and writing an answer back to the owner::

    python scripts/freebuff_inbox.py --answer --reply-to <id> --text "..."
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from .config import config_dir

INBOX_NAME = "freebuff_inbox.jsonl"

# Keep the newest N entries and never let the file grow past the byte cap.
MAX_ENTRIES = 500
MAX_BYTES = 256 * 1024

# A single handoff is a note, not a file transfer.
MAX_TEXT_CHARS = 4000

# Which way a record travels. Kept as constants because the reader, the writer,
# the PC-side CLI and the tests all have to agree on the spelling.
KIND_NOTE = "note"  # owner -> Freebuff
KIND_ANSWER = "answer"  # Freebuff -> owner
KINDS = (KIND_NOTE, KIND_ANSWER)

# Credential shapes, scrubbed BEFORE anything is written. These are the
# prefixes actually seen in the wild for the services this box talks to
# (Groq, OpenAI-compatible/DeepSeek, GitHub, Telegram bots, Aali's own
# master key), plus generic `key = "..."` assignments and long hex blobs.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # gsk_..., ghp_..., sk-..., sk_..., xoxb-..., AKIA..., hf_...
    (re.compile(r"\b(?:gsk|ghp|gho|ghu|ghs|xox[abps]|AKIA|hf)[-_][A-Za-z0-9_\-]{8,}"), "«مفتاح»"),
    # an OpenAI-style key with a dash but no alphabetic prefix marker
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "«مفتاح»"),
    # A Telegram bot token: 123456789:AAE...  (only when it is not our chat id)
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}"), "«توكن»"),
    # key/secret/token/password assignments, quoted or not
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|bot[_-]?token)"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9_\-\./+=]{8,})[\"']?"
        ),
        r"\1=«محجوب»",
    ),
    # the master's own key file convention
    (re.compile(r"\bu<[A-Za-z0-9]{16,}>\b"), "«مفتاح»"),
    # long hex blobs are almost never prose
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "«hex»"),
)


def scrub_secrets(text: str) -> tuple[str, int]:
    """Return ``(clean_text, n_replacements)``.

    A replacement keeps the surrounding shape so the owner can still see that
    *something* was removed, but never the value itself.
    """
    clean = text or ""
    hits = 0
    for pattern, repl in _SECRET_PATTERNS:
        clean, n = pattern.subn(repl, clean)
        hits += n
    return clean, hits


def inbox_path(cfg_dir: str | Path | None = None) -> Path:
    """Where the inbox lives — the same config dir as every other secret.

    Honouring ``AALI_JARVIS_CONFIG`` is not a convenience: it is what keeps the
    test suite from writing into the owner's real ``~/.aali`` and keeps the
    inbox next to the token it belongs with.
    """
    if cfg_dir:
        return Path(cfg_dir).expanduser() / INBOX_NAME
    return config_dir() / INBOX_NAME


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # Windows / exotic mounts — not fatal


def _trim(path: Path) -> None:
    """Keep the file bounded: newest MAX_ENTRIES lines and under MAX_BYTES."""
    try:
        if not path.exists():
            return
        if path.stat().st_size <= MAX_BYTES:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) <= MAX_ENTRIES:
                return
        else:
            # Too big to even read comfortably: stream the tail back.
            with path.open("rb") as fh:
                fh.seek(max(0, path.stat().st_size - MAX_BYTES))
                chunk = fh.read().decode("utf-8", errors="replace")
            lines = chunk.splitlines()[1:]  # first line is probably cut in half
        kept = lines[-MAX_ENTRIES:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        os.replace(tmp, path)
        _chmod(path, 0o600)
    except OSError:
        pass


def append(
    text: str,
    cfg_dir: str | Path | None = None,
    origin: str = "telegram",
    kind: str = KIND_NOTE,
    reply_to: str | None = None,
    machine: str | None = None,
) -> dict[str, Any] | None:
    """Write one handoff record. Returns the stored record, or ``None``.

    ``None`` means "there was nothing worth sending" — an empty message is not
    a handoff, and Jarvis must not pretend it delivered one.

    ``kind`` defaults to a note *towards Freebuff*. The owner never chooses it
    from Telegram: an order he speaks becomes a note, and only Freebuff writes
    the ``answer`` that comes back.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    clean, secrets_masked = scrub_secrets(raw)
    clean = clean.strip()
    if not clean:
        return None
    if len(clean) > MAX_TEXT_CHARS:
        clean = clean[:MAX_TEXT_CHARS] + " …(truncated)"

    path = inbox_path(cfg_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass

    record: dict[str, Any] = {
        "id": secrets.token_hex(4),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(time.time()),
        "origin": origin,
        "kind": kind if kind in KINDS else KIND_NOTE,
        "text": clean,
        "secrets_masked": secrets_masked,
    }
    # Both are optional metadata. They are recorded as fields rather than
    # folded into the text so the payload stays verbatim: Freebuff must see the
    # owner's exact words, not "task for pc: <words>".
    if reply_to:
        record["reply_to"] = str(reply_to)[:32]
    if machine:
        record["machine"] = str(machine)[:32]
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        _chmod(path, 0o600)
    except OSError:
        return None
    _trim(path)
    return record


def read_all(
    cfg_dir: str | Path | None = None,
    limit: int = 20,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    """Newest-last list of handoff notes (damaged lines are skipped)."""
    path = inbox_path(cfg_dir)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("text"):
            out.append(rec)
    if unread_only:
        seen = {r.get("id") for r in out if r.get("handled")}
        out = [r for r in out if r.get("id") not in seen]
    return out[-limit:]


def mark_handled(
    ids: list[str], cfg_dir: str | Path | None = None
) -> int:
    """Flag notes as delivered so ``unread_only`` stops returning them.

    Rewrites the file in place; an unwritable inbox is not an error worth
    crashing the bot over, so the count of what could be marked is returned.
    """
    wanted = {i for i in ids if i}
    if not wanted:
        return 0
    path = inbox_path(cfg_dir)
    if not path.exists():
        return 0
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    kept: list[str] = []
    marked = 0
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except ValueError:
            kept.append(stripped)
            continue
        if isinstance(rec, dict) and rec.get("id") in wanted and not rec.get("handled"):
            rec["handled"] = time.strftime("%Y-%m-%d %H:%M:%S")
            marked += 1
        kept.append(json.dumps(rec, ensure_ascii=False))
    if not marked:
        return 0
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        os.replace(tmp, path)
        _chmod(path, 0o600)
    except OSError:
        return 0
    return marked


def pending_count(cfg_dir: str | Path | None = None) -> int:
    return len(read_all(cfg_dir, limit=MAX_ENTRIES, unread_only=True))


def of_kind(
    kind: str,
    cfg_dir: str | Path | None = None,
    limit: int = 20,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    """Records of one direction. Records written before ``kind`` existed are
    treated as notes, because every one of them was a note."""
    records = read_all(cfg_dir, limit=MAX_ENTRIES, unread_only=unread_only)
    wanted = kind if kind in KINDS else KIND_NOTE
    picked = [r for r in records if r.get("kind", KIND_NOTE) == wanted]
    return picked[-limit:] if limit and limit > 0 else picked


def read_answers(
    cfg_dir: str | Path | None = None,
    limit: int = 20,
    unread_only: bool = True,
) -> list[dict[str, Any]]:
    """What Freebuff wrote back, oldest first."""
    return of_kind(KIND_ANSWER, cfg_dir, limit=limit, unread_only=unread_only)


# --------------------------------------------------------------------- CLI
def _print(records: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return
    if not records:
        print("inbox empty — nothing sent to Freebuff yet")
        return
    for rec in records:
        flag = " (read)" if rec.get("handled") else ""
        masked = f" [secrets masked: {rec['secrets_masked']}]" if rec.get("secrets_masked") else ""
        print(f"--- #{rec.get('id')} {rec.get('ts')}{flag}{masked}")
        print(rec.get("text", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis.handoff",
        description="Read the Freebuff handoff inbox (run on the Pi).",
    )
    parser.add_argument("--config-dir", default=None, help="Jarvis config dir")
    parser.add_argument("--limit", type=int, default=20, help="how many notes")
    parser.add_argument("--all", action="store_true", help="include handled notes")
    parser.add_argument("--take", action="store_true", help="mark the listed notes handled")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--path", action="store_true", help="print the inbox path only")
    parser.add_argument(
        "--send",
        metavar="TEXT",
        default=None,
        help="append a note to the inbox and exit (used to test the bridge)",
    )
    parser.add_argument(
        "--answer",
        action="store_true",
        help="append an ANSWER (Freebuff -> owner) instead of a note",
    )
    parser.add_argument(
        "--reply-to",
        metavar="ID",
        default=None,
        help="with --answer: the id of the note being answered",
    )
    parser.add_argument(
        "--text", default=None, metavar="TEXT", help="with --answer: the reply body"
    )
    parser.add_argument(
        "--answers",
        action="store_true",
        help="only the records Freebuff wrote back (not the owner's notes)",
    )
    args = parser.parse_args(argv)

    path = inbox_path(args.config_dir)
    if args.path:
        print(path)
        return 0

    if args.send is not None or args.answer or args.text is not None:
        text = args.send or args.text or ""
        if not text:
            print("--answer needs text (pass it after --answer)")
            return 1
        rec = append(
            text,
            cfg_dir=args.config_dir,
            origin="freebuff" if args.answer else "cli",
            kind=KIND_ANSWER if args.answer else KIND_NOTE,
            reply_to=args.reply_to,
        )
        if rec is None:
            print("nothing to send (empty text)")
            return 1
        word = "answered" if args.answer else "queued"
        print(f"{word} #{rec['id']} ({len(rec['text'])} chars) -> {path}")
        if rec.get("secrets_masked"):
            print(f"warning: masked {rec['secrets_masked']} secret-looking span(s)")
        return 0

    records = (
        read_answers(args.config_dir, limit=args.limit, unread_only=not args.all)
        if args.answers
        else read_all(args.config_dir, limit=args.limit, unread_only=not args.all)
    )
    if args.answers and not records:
        print("no answers from Freebuff yet")
        return 0
    _print(records, args.json)
    if args.take and records:
        marked = mark_handled([r["id"] for r in records if r.get("id")], args.config_dir)
        if not args.json:
            print(f"\nmarked {marked} note(s) as delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
