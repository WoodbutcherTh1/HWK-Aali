"""آلي memory review — what does Aali remember about you, and full control.

Owner-facing tool (2026-09-06 request): see every long-term memory entry with
its date and topic, edit, delete, and search. Nothing here is reachable from
chat — only Hmam on this machine can change memory through this CLI
(Aali's own `memory forget` still goes through the chat confirmation gate).

Usage:
  python scripts/memory_review.py                     # list everything
  python scripts/memory_review.py --topic git         # filter by topic
  python scripts/memory_review.py --kind decision     # filter by kind
  python scripts/memory_review.py --search "github"   # keyword search
  python scripts/memory_review.py --show <id>         # one entry, full detail
  python scripts/memory_review.py --edit <id> --text "new text"
  python scripts/memory_review.py --edit <id> --topic "new topic" (or --clear-topic)
  python scripts/memory_review.py --edit <id> --kind preference
  python scripts/memory_review.py --delete <id>       # remove one entry
  python scripts/memory_review.py --stats             # counts by kind + latest
  python scripts/memory_review.py --conversation-log  # raw chat turns on disk

Every destructive/editing action prints before/after and requires the entry
to exist; nothing is ever silently changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "file-agent"))

from file_agent import memory  # noqa: E402

KIND_LABELS = {
    "decision": "قرار",
    "preference": "تفضيل",
    "fact": "معلومة",
    "instruction": "تعليمة",
}


def _entry_row(entry: dict) -> str:
    """One-line table row: id, date, kind, topic, text preview."""
    when = str(entry.get("updated", ""))[:16].replace("T", " ")
    kind = KIND_LABELS.get(str(entry.get("kind")), str(entry.get("kind")))
    topic = entry.get("topic") or "—"
    confirm = int(entry.get("confirmations", 1))
    text = str(entry.get("text", ""))
    preview = text if len(text) <= 70 else text[:67] + "..."
    auto = " (تلقائي)" if entry.get("auto") else ""
    return f"{entry.get('id', '?'):>10}  {when}  {kind:<9} {topic:<14} x{confirm}{auto}  {preview}"


HEADER = f"{'id':>10}  {'آخر تحديث':<16}  {'النوع':<9} {'الموضوع':<14} {'عدد':<4}  النص"


def _print_entries(entries: list[dict], title: str) -> None:
    print(f"\n{title} — {len(entries)} entries (newest first)")
    print(HEADER)
    print("-" * len(HEADER))
    for entry in sorted(entries, key=lambda e: e.get("updated", ""), reverse=True):
        print(_entry_row(entry))


def cmd_list(args: argparse.Namespace) -> int:
    result = memory.recall(query=args.search or "", topic=args.topic,
                           kind=args.kind, limit=max(args.limit, 1))["result"]
    entries = result["entries"]
    if not entries:
        print("Aali's memory is empty (or nothing matched the filters) — "
              "الذاكرة فارغة أو لا يوجد تطابق.")
        return 0
    title = "ما يتذكره آلي عنك / What Aali remembers"
    filters = [f"{k}={v}" for k, v in (("topic", args.topic), ("kind", args.kind),
                                       ("search", args.search)) if v]
    if filters:
        title += " [" + ", ".join(filters) + "]"
    _print_entries(entries, title)
    if result.get("total", len(entries)) > len(entries):
        print(f"\n(showing {len(entries)} of {result['total']} — narrow with "
              "--topic/--kind/--search or raise --limit)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    entry = memory.get(args.entry_id)["result"]["entry"]
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    if all(v is None for v in (args.text, args.topic, args.kind)) and not args.clear_topic:
        print("Nothing to change: pass --text, --topic, --clear-topic and/or --kind.")
        return 2
    before = memory.get(args.entry_id)["result"]["entry"]
    result = memory.edit(
        args.entry_id,
        text=args.text,
        topic=args.topic,
        kind=args.kind,
        clear_topic=args.clear_topic,
    )["result"]
    if result["action"] == "merged":
        print(f"Merged into existing duplicate {result['merged_into']} "
              "(confirmations bumped); the old entry is gone.")
    print(f"\nBEFORE: {_entry_row(before)}")
    print(f"AFTER:  {_entry_row(result['entry'])}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    before = memory.get(args.entry_id)["result"]["entry"]
    print("Deleting:\n  " + _entry_row(before))
    result = memory.forget(entry_id=args.entry_id)["result"]
    print(f"Deleted {result['count']} entry. Aali no longer remembers it.")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    result = memory.summary()["result"]
    print(f"\nTotal: {result['total']} entries")
    for kind, count in sorted(result["by_kind"].items()):
        print(f"  {KIND_LABELS.get(kind, kind):<9} {count}")
    if result["latest"]:
        print("\nLatest 5:")
        for item in result["latest"]:
            when = str(item.get("updated", ""))[:16].replace("T", " ")
            topic = item.get("topic") or "—"
            print(f"  {item['id']:>10}  {when}  {KIND_LABELS.get(str(item['kind']), item['kind']):<9} "
                  f"{topic:<14} {str(item['text'])[:60]}")
    return 0


def cmd_conversation_log(args: argparse.Namespace) -> int:
    path = memory.conversation_log_path()
    if not path.exists():
        print(f"No conversation log yet at {path}")
        return 0
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if args.limit:
        lines = lines[-int(args.limit):]
    print(f"\nRaw conversation turns on disk: {path} ({len(lines)} shown)")
    for line in lines:
        record = json.loads(line)
        when = str(record.get("ts", ""))[:19].replace("T", " ")
        role = "المستخدم" if record.get("role") == "user" else "آلي"
        print(f"  {when}  [{role}] {str(record.get('text', ''))[:90]}")
    print("\nThese raw turns are what scripts/teacher_to_sft.py turns into "
          "training data later.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review and manage what Aali remembers about you.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[0] if __doc__ else None,
    )
    parser.add_argument("--topic", help="filter by topic (list mode)")
    parser.add_argument("--kind", choices=memory.KINDS, help="filter by kind")
    parser.add_argument("--search", help="keyword search in entry text")
    parser.add_argument("--limit", type=int, default=200, help="max entries to list")
    parser.add_argument("--show", dest="entry_id", metavar="ID",
                        help="show one entry in full detail")
    parser.add_argument("--edit", dest="edit_id", metavar="ID",
                        help="edit an entry (with --text/--topic/--kind/--clear-topic)")
    parser.add_argument("--text", help="new text for --edit")
    parser.add_argument("--new-topic", dest="topic", help="new topic for --edit")
    parser.add_argument("--clear-topic", action="store_true", help="remove the topic")
    parser.add_argument("--set-kind", dest="set_kind", choices=memory.KINDS,
                        help="new kind for --edit")
    parser.add_argument("--delete", dest="delete_id", metavar="ID",
                        help="delete one entry (prints it first)")
    parser.add_argument("--stats", action="store_true", help="counts by kind + latest")
    parser.add_argument("--conversation-log", action="store_true",
                        help="show raw stored chat turns (last N with --limit)")
    args = parser.parse_args()

    # Normalise --edit flags into the edit call's vocabulary.
    if args.edit_id:
        args.entry_id = args.edit_id
        args.kind = args.set_kind
    try:
        if args.entry_id or args.edit_id:
            return cmd_edit(args) if args.edit_id else cmd_show(args)
        if args.delete_id:
            args.entry_id = args.delete_id
            return cmd_delete(args)
        if args.stats:
            return cmd_stats(args)
        if args.conversation_log:
            return cmd_conversation_log(args)
        return cmd_list(args)
    except memory.MemoryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
