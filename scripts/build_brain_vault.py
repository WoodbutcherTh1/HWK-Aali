"""Export the آلي Brain Tree as an Obsidian vault (OWNER ONLY).

Usage:
    python scripts/build_brain_vault.py               # default: D:/hwk-data/aali-brain-vault
    python scripts/build_brain_vault.py --out X:/hwk-backups/aali-brain

Every flow becomes one note; every note wiki-links to its related flows and
the ROOT note links them all — open the folder as an Obsidian vault and view
Graph View to see the whole brain as a living tree.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

from file_agent.brain_tree import render_ascii, render_obsidian  # noqa: E402

DEFAULT_OUT = Path("D:/hwk-data/aali-brain-vault")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Aali brain tree as an Obsidian vault.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Vault output folder")
    parser.add_argument("--print", action="store_true", help="Also print the ASCII tree")
    args = parser.parse_args()

    vault = Path(args.out)
    written = render_obsidian(vault)
    print(f"Obsidian vault written: {vault}")
    for path in written:
        print(f"  • {path.name}")
    print("Open this folder in Obsidian → Graph View to see the tree.")
    if args.print:
        print()
        print(render_ascii())


if __name__ == "__main__":
    main()
