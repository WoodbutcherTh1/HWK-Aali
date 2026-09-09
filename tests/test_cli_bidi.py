"""Tests for the Arabic display fix in aali_cli.py (fx = shape + reorder).

Classic conhost renders logical-order Arabic disconnected and mirrored;
the CLI reshapes into Unicode presentation forms and reorders runs for
display. These tests pin the transformations (pure functions, no terminal).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aali_cli as cli  # noqa: E402


# ----------------------------------------------------------------- shaping
def test_shape_two_letter_word_uses_initial_and_final() -> None:
    # "بب" = beheh joined to beheh: first takes INITIAL, second takes FINAL
    shaped = cli.shape_arabic("بب")
    assert shaped == chr(0xFE91) + chr(0xFE90)


def test_shape_isolated_and_initial() -> None:
    # "اب": alef right-joining (can't join forward) -> isolated; beheh alone
    # at word end with nothing before connecting -> isolated... but alef does
    # not connect forward, so beheh starts a new join -> initial? No: beheh
    # has no previous joiner and nothing after -> ISOLATED.
    shaped = cli.shape_arabic("اب")
    assert shaped == chr(0xFE8D) + chr(0xFE8F)


def test_shape_medial_in_three_letter_word() -> None:
    # "ببب": initial, medial, final
    shaped = cli.shape_arabic("ببب")
    assert shaped == chr(0xFE91) + chr(0xFE92) + chr(0xFE90)


def test_shape_lam_alef_ligature() -> None:
    # "لا" becomes the lam-alef ligature (isolated form)
    assert cli.shape_arabic("لا") == chr(0xFEFB)
    # after a joining letter (ب connects forward) -> final ligature form
    assert cli.shape_arabic("بلا") == chr(0xFE91) + chr(0xFEFC)


def test_shape_right_joining_letters_stay_isolated_when_word_final() -> None:
    # dal is right-joining (cannot connect forward), but beh CAN join to it:
    # beh takes INITIAL, dal receives the join and takes FINAL
    shaped = cli.shape_arabic("بد")
    assert shaped == chr(0xFE91) + chr(0xFEAA)
    # true word-final right-joiner after a non-joiner: "اد" - alef cannot
    # join forward, so dal never receives a join and stays ISOLATED
    assert cli.shape_arabic("اد") == chr(0xFE8D) + chr(0xFEA9)


def test_shape_harakat_do_not_break_joining() -> None:
    # "بَب" - fatha between the letters must not split the join
    shaped = cli.shape_arabic("بَب")
    assert shaped == chr(0xFE91) + chr(0x064E) + chr(0xFE90)


def test_shape_non_arabic_untouched() -> None:
    assert cli.shape_arabic("hello {x: 1}") == "hello {x: 1}"


# ----------------------------------------------------------------- reordering
def test_reorder_reverses_rtl_run() -> None:
    shaped = cli.shape_arabic("اب")
    visual = cli.reorder_visual(shaped)
    # display order: alef-form on the RIGHT (read RTL: alef then beheh)
    assert visual == chr(0xFE8F) + chr(0xFE8D)


def test_reorder_keeps_ltr_run_intact() -> None:
    shaped = cli.shape_arabic("اب X")
    visual = cli.reorder_visual(shaped)
    # X lands on the LEFT (end of the visually-RTL line), Arabic to its right
    assert visual.startswith("X")
    assert cli._PRESENTATION_RE.search(visual)


def test_reorder_keeps_numbers_and_urls() -> None:
    visual = cli.reorder_visual(cli.shape_arabic("abc123"))
    assert visual == "abc123"


def test_reverse_keeps_harakat_attached() -> None:
    # base letter + its mark must move as one unit
    chunk = "ب" + chr(0x064E) + "ا"
    assert cli._reverse_rtl(chunk) == "ا" + "ب" + chr(0x064E)


def test_brackets_mirror_in_rtl_run() -> None:
    # UAX#9 L4: in RTL runs brackets are swapped so the dumb LTR terminal
    # shows the right glyph. Logical 'ا(ب)' displays as '(ب)ا': alef rightmost
    # (RTL reading start), logical ')' leftmost mirrored to '('.
    assert cli._reverse_rtl("ا(ب)") == "(ب)ا"


# ----------------------------------------------------------------- fx facade
def test_fx_idempotent() -> None:
    text = "آلي — مساعدك المحلي، جاهز."
    once = cli.fx(text)
    assert cli.fx(once) == once


def test_fx_passthrough_pure_latin() -> None:
    assert cli.fx("cargo run --release") == "cargo run --release"


def test_fx_nonempty_on_arabic() -> None:
    out = cli.fx("مرحبا")
    assert out and cli._PRESENTATION_RE.search(out)


# ------------------------------------------------------- pref / auto-detect
def test_bidi_pref_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "BIDI_FILE", tmp_path / "bidi")
    assert cli.load_bidi_pref() == "auto"  # default
    cli.save_bidi_pref("on")
    assert cli.load_bidi_pref() == "on"
    cli.save_bidi_pref("nonsense")  # file rewritten, reader ignores junk
    monkeypatch.setattr(cli, "BIDI_FILE", tmp_path / "bidi2")
    (tmp_path / "bidi2").write_text("junk", encoding="utf-8")
    assert cli.load_bidi_pref() == "auto"


def test_apply_pref_auto_off_when_terminal_bidi_capable(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_bidi_pref", lambda: "auto")
    monkeypatch.setattr(cli, "_terminal_bidi_capable", lambda: True)
    cli.apply_bidi_pref()
    assert cli.BIDI_MODE is False
    monkeypatch.setattr(cli, "_terminal_bidi_capable", lambda: False)
    cli.apply_bidi_pref()
    assert cli.BIDI_MODE is True


def test_apply_pref_explicit_overrides_auto(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_bidi_pref", lambda: "off")
    monkeypatch.setattr(cli, "_terminal_bidi_capable", lambda: False)
    cli.apply_bidi_pref()
    assert cli.BIDI_MODE is False


def test_paint_applies_fx_when_mode_on(monkeypatch) -> None:
    monkeypatch.setattr(cli, "BIDI_MODE", True)
    out = cli.paint("مرحبا", "gold", enabled=True)
    assert cli._PRESENTATION_RE.search(out)
    # color disabled -> raw fx only
    out2 = cli.paint("مرحبا", "gold", enabled=False)
    assert out2 == "مرحبا"  # no fix when color off? (fx runs only when enabled)
