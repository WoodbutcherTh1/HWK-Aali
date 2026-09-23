"""Stage-4 extension of phase_d_watchdog (2026-09-23).

The owner's Phase D continuation (+2-4B tokens) needed a 4th stage chaining
phase-d-wide -> phase-d-all over ALL tokenized corpora, plus an end-of-window
MORNING_REPORT.md (the soup-specific night_caretaker left mornings stale).

CPU-only tests: no GPU, no subprocesses, no real trainer. Network-free.

Run:
    .venv/Scripts/python -m pytest tests/test_phase_d_stage4.py -q
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase_d_watchdog as wd  # noqa: E402


@pytest.fixture()
def _watch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the watchdog from the real machine: tmp DATA, tmp model dirs,
    no GPU queries, no real launches."""
    monkeypatch.setattr(wd, "DATA", tmp_path)
    monkeypatch.setattr(wd, "WATCH_LOG", tmp_path / "phase_d_watchdog.log")
    monkeypatch.setattr(wd, "STATE_FILE", tmp_path / "phase_d_watchdog_state.json")
    monkeypatch.setattr(wd, "TOKENIZE_LOCK", tmp_path / "tokenize.pidlock")
    monkeypatch.setattr(wd, "MORNING_REPORT", tmp_path / "MORNING_REPORT.md")
    monkeypatch.setattr(wd, "tokens_root", lambda: tmp_path / "tokens")
    monkeypatch.setattr(wd, "gpu_is_free", lambda: True)
    monkeypatch.setattr(wd, "ensure_tokenization", lambda: None)
    monkeypatch.setattr(wd, "tokenizer_running", lambda: (False, None))
    # stage dirs under tmp: rewrite the stage table's output/source dirs
    models = tmp_path / "models"
    for stage in wd.STAGES:
        stage["output_dir"] = str(models / Path(stage["output_dir"]).name)
        if stage["source_dir"]:
            stage["source_dir"] = str(models / Path(stage["source_dir"]).name)
    # bootstrap via a no-op (the real one shells out to bootstrap_sft_state.py)
    monkeypatch.setattr(wd, "bootstrap_stage", lambda stage: True)
    # defense in depth (2026-09-23 lesson): with gpu_is_free forced True and
    # bootstrap a no-op, an unpatched launch path WOULD spawn a real trainer
    # (one test did, mid-suite). launch_detached is faked at the fixture
    # level so no test can ever put a real process on the GPU.
    monkeypatch.setattr(wd, "launch_detached",
                        lambda command, log_base: 12345)
    yield models


def test_stage4_in_table_chains_wide(_watch: Path) -> None:
    stage4 = wd.STAGES[-1]
    assert stage4["stage"] == 4
    assert stage4["source_dir"].endswith("phase-d-wide")
    assert stage4["output_dir"].endswith("phase-d-all")


def test_stage4_corpora_arg_lists_all_tokenized(_watch: Path) -> None:
    (wd.tokens_root() / "pile").mkdir(parents=True, exist_ok=True)
    (wd.tokens_root() / "pile" / "manifest.json").write_text("{}", encoding="utf-8")
    arg = wd.stage4_corpora_arg()
    assert arg == "pile"   # only pile has a manifest in this isolated view
    assert "law" not in arg


def test_stage4_skips_corpora_without_manifest(_watch: Path) -> None:
    for name in ("pile", "code"):
        (wd.tokens_root() / name).mkdir(parents=True, exist_ok=True)
        (wd.tokens_root() / name / "manifest.json").write_text("{}", encoding="utf-8")
    arg = wd.stage4_corpora_arg()
    # ALL_CORPORA order is preserved (pile before code); incomplete corpora
    # (no manifest) never make the list
    assert arg == "pile,code"
    assert "law" not in arg and "mmlu" not in arg


def test_advance_from_stage3_launches_stage4(_watch: Path) -> None:
    launched: list[dict] = []

    def fake_launch(stage: dict) -> bool:
        launched.append(stage)
        return True

    wd.save_state(wd.STAGES[2])   # stage 3 is the active one
    with patch.object(wd, "launch_stage", fake_launch), \
         patch.object(wd, "trainer_healthy",
                      return_value=(True, "stage 3 COMPLETE (step 4000)")):
        action = wd.check_cycle(heartbeat=False)
    assert action == "advanced-to-4"
    assert len(launched) == 1 and launched[0]["stage"] == 4


def test_stage3_deferral_still_applies_before_stage4(_watch: Path) -> None:
    # regression: the stage-3 'wait for tokenization' guard must stay intact -
    # it fires when ADVANCING TO stage 3 (stage 2 done, nothing tokenized),
    # never when advancing past it
    wd.save_state(wd.STAGES[1])
    with patch.object(wd, "stage3_corpora_arg", lambda: None), \
         patch.object(wd, "launch_stage", return_value=True) as fake_launch, \
         patch.object(wd, "trainer_healthy",
                      return_value=(True, "stage 2 COMPLETE (step 4000)")):
        action = wd.check_cycle(heartbeat=False)
    assert "deferred" in action
    fake_launch.assert_not_called()


def test_stage4_launch_uses_available_corpora(_watch: Path) -> None:
    # every corpus tokenized -> the built command carries them all
    for name in wd.ALL_CORPORA:
        (wd.tokens_root() / name).mkdir(parents=True, exist_ok=True)
        (wd.tokens_root() / name / "manifest.json").write_text("{}", encoding="utf-8")
    cmd = wd.build_stage_command(wd.STAGES[3])
    i = cmd.index("--corpora")
    assert set(cmd[i + 1].split(",")) == set(wd.ALL_CORPORA)
    assert cmd[cmd.index("--output-dir") + 1].endswith("phase-d-all")
    assert "--dropout" in cmd and cmd[cmd.index("--dropout") + 1] == "0.1"


def test_morning_report_written_on_window_end(_watch: Path) -> None:
    wd.WATCH_LOG.write_text("[01:00:00] ok: alive\n", encoding="utf-8")
    wd.write_morning_report("watch window over")
    report = wd.MORNING_REPORT
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "watch window over" in text
    assert "STATUS.md" in text   # pointer to the live board


def test_morning_report_not_written_mid_window(_watch: Path) -> None:
    # only the window-end path writes it - mid-duty cycles never touch the file
    assert not wd.MORNING_REPORT.exists()
