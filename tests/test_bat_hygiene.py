"""Bat-file hygiene (2026-09-22 owner lesson).

cmd.exe misparses .bat files with Unix (LF) line endings - it slices
commands mid-word ('ens' from 'set', 'ho.' from 'echo') - and garbles on
non-ASCII bytes. 22 of 26 scripts/*.bat shipped that way and
aali_desktop.bat crashed on double-click. These tests keep every
repository .bat file ASCII + CRLF forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

BATS = sorted(SCRIPTS.glob("*.bat"))


def test_bat_files_exist():
    assert len(BATS) >= 10, f"expected the scripts/*.bat fleet, found {len(BATS)}"


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_bat_is_ascii_with_crlf(bat: Path):
    data = bat.read_bytes()

    nonascii = sum(1 for b in data if b > 127)
    assert nonascii == 0, (
        f"{bat.name}: {nonascii} non-ASCII byte(s) - cmd.exe garbles them; "
        "keep .bat files ASCII-only (Arabic text belongs in docs/comments)"
    )

    bare_lf = data.count(b"\n") - data.count(b"\r\n")
    assert bare_lf == 0, (
        f"{bat.name}: {bare_lf} bare-LF line ending(s) - cmd.exe slices "
        "commands mid-word; save .bat files with CRLF endings"
    )
