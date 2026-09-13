"""Regression: SFT resume from a BOOTSTRAPPED trainer state must not crash.

2026-09-14 lesson: the first real Phase C launch died at the resume point -
train_scratch.py called optimizer.load_state_dict(state["optimizer"])
unconditionally, but scripts/bootstrap_sft_state.py writes
{"optimizer": None, "step": 0} BY DESIGN (pretrained weights + a FRESH
optimizer so the SFT's own cosine schedule runs from step 0). The
AttributeError: 'NoneType' object has no attribute 'copy' from torch's
optimizer.load_state_dict killed the run before a single step.

train_scratch.py is a monolith (no importable resume function), so like the
other source-level tripwires in this suite this pins the contract in text:
the resume block must treat a None optimizer state as a documented
"fresh optimizer at step 0" path, and bootstrap_sft_state.py must keep
writing the matching state shape.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAINER = ROOT / "train_scratch.py"
BOOTSTRAP = ROOT / "scripts" / "bootstrap_sft_state.py"


def _args_resume_block() -> str:
    """Return the `if args.resume:` block of train_scratch.py as source text."""
    source = TRAINER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    def is_args_resume(test: ast.expr) -> bool:
        # matches both `if args.resume:` (bare store_true flag) and
        # a future `if args.resume and ...:` (Compare) form
        attr = test.left if isinstance(test, ast.Compare) else test
        return (isinstance(attr, ast.Attribute)
                and isinstance(attr.value, ast.Name)
                and attr.value.id == "args"
                and attr.attr == "resume")

    for node in ast.walk(tree):
        if isinstance(node, ast.If) and is_args_resume(node.test):
            segment = ast.get_source_segment(source, node)
            if segment is None:  # pragma: no cover - positions always present
                break
            return segment
    raise AssertionError("no `if args.resume:` block found in train_scratch.py")


def test_resume_tolerates_none_optimizer_state() -> None:
    """The 2026-09-14 Phase C crash: None optimizer state must be a handled
    branch (fresh optimizer at step 0), never an unconditional load."""
    block = _args_resume_block()
    assert "optimizer.load_state_dict" in block, (
        "resume block no longer restores the optimizer - inspect manually")
    assert 'state.get("optimizer") is not None' in block, (
        "resume must guard optimizer.load_state_dict against a None state "
        "(bootstrapped SFT state writes optimizer: None on purpose)")
    assert "fresh optimizer" in block, (
        "the None branch should say what it did (fresh optimizer at step 0) "
        "so the trainer log explains the choice")


def test_resume_still_loads_a_real_optimizer_state() -> None:
    """The guard must not weaken the normal path: a real (non-None) state is
    still loaded via optimizer.load_state_dict inside the guarded branch."""
    block = _args_resume_block()
    assert 'optimizer.load_state_dict(state["optimizer"])' in block, (
        "normal resume must keep loading the saved optimizer state_dict")


def test_bootstrap_contract_unchanged() -> None:
    """bootstrap_sft_state.py must keep writing the None-optimizer state this
    regression protects: {"optimizer": None, ... step 0} in trainer-state.pt.
    If this test fails the bootstrap shape changed - revisit the trainer
    guard together with it."""
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert '"optimizer": None' in source, (
        "bootstrap no longer writes a None optimizer state - the trainer's "
        "fresh-optimizer branch may be dead code now")
    assert '"step": 0' in source, (
        "bootstrap must keep step=0 so the SFT cosine schedule starts fresh")
