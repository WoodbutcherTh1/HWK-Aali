"""Smoke-test the local no-key file-agent flow."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from agent_loop import agent_loop


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hwk-agent-") as directory:
        root = Path(directory)
        commands = [
            "أنشئ ملف test.txt واكتب بداخله مرحباً",
            "اقرأ test.txt",
            "أضف سطر جديد إلى test.txt",
            "احذف الملف test.txt",
        ]
        for command in commands:
            print(f"\n> {command}")
            print(agent_loop(command, root, mode="local", print_final=False))
        print(f"\nWorkspace cleaned: {not any(root.iterdir())}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()