"""Tests for the mission clock dashboard (2026-09-12 watch-party request).

The dashboard must be FUN but HONEST: every number is parsed from the real
logs (soup_train_live.log tqdm lines, pipeline stage tails, the verdict
report), the countdown comes from the trainer's own ETA, and no test ever
touches the network (the brain probe is injected). CPU-only, tmp files.

Run:
    .venv/Scripts/python -m pytest tests/test_mission_clock.py -q
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mission_clock as mc  # noqa: E402


# ---------------------------------------------------------------------------
# Parsers against REAL log samples (2026-09-12 attempt 7 / salvage)
# ---------------------------------------------------------------------------

TQDM_SAMPLE = (
    "{'loss': '0.6442', 'grad_norm': '0.3359', 'epoch': '2.295'}\n"
    " 77%|███████▋  | 2927/3804 [3:16:44<1:02:28, 4.27s/it]\n"
)


def test_parse_tqdm_reads_progress_and_eta() -> None:
    progress = mc.parse_tqdm(TQDM_SAMPLE)
    assert progress is not None
    assert progress["pct"] == 77
    assert progress["step"] == 2927
    assert progress["total"] == 3804
    assert progress["eta_s"] == 3600 + 2 * 60 + 28  # 1:02:28
    assert progress["rate"] == 4.27
    assert progress["unit"] == "s/it"


def test_parse_tqdm_takes_the_last_line() -> None:
    text = (" 10%|█         | 100/3804 [0:05:00<0:45:00, 0.7s/it]\n"
            " 77%|███████▋  | 2927/3804 [3:16:44<1:02:28, 4.27s/it]\n")
    progress = mc.parse_tqdm(text)
    assert progress is not None and progress["step"] == 2927


def test_parse_tqdm_it_per_s_has_no_eta_seconds() -> None:
    progress = mc.parse_tqdm(" 50%|█████     | 5/10 [0:01:00<0:01:00, 2.00it/s]")
    assert progress is not None
    assert progress["unit"] == "it/s"
    assert progress["eta_s"] is None


def test_parse_tqdm_none_on_garbage() -> None:
    assert mc.parse_tqdm("no progress here") is None


def test_parse_loss_takes_the_last_value() -> None:
    text = ("{'loss': '0.8714', 'epoch': '2.24'}\n"
            "{'loss': '0.6442', 'epoch': '2.295'}\n")
    assert mc.parse_loss(text) == 0.6442
    assert mc.parse_loss("nothing") is None


EXAM_SAMPLE = ("[1/26] img_basic_en ...\n    -> FAIL\n"
               "[2/26] img_basic_ar ...\n    -> PASS\n")


def test_parse_exam_reads_case_progress() -> None:
    assert mc.parse_exam(EXAM_SAMPLE) == (2, 26)
    assert mc.parse_exam("no exam here") is None


def test_parse_stage_line_utc_stamp() -> None:
    parsed = mc.parse_stage_line(
        "[2026-09-12 13:29:13] starting Soup server on port 20129")
    assert parsed is not None
    message, stamp = parsed
    assert message == "starting Soup server on port 20129"
    assert stamp.tzinfo is not None
    assert stamp.utcoffset().total_seconds() == 0  # UTC


def test_last_stage_skips_pytest_and_unstamped_lines() -> None:
    # stamp relative to NOW so the age assertion never depends on wall time
    stamp = (datetime.now(timezone.utc)
             - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    text = (f"[{stamp}] === soup pipeline start ===\n"
            "C:\\Temp\\pytest-of-HmamK\\x verdict: PROMOTE\n"
            "plain line with no stamp\n"
            f"[{stamp}] starting Soup server on port 20129\n")
    stage = mc.last_stage(text)
    assert stage is not None
    message, age = stage
    assert "Soup server" in message
    assert 25 <= age <= 90


def test_verdict_from_report() -> None:
    assert mc.verdict_from_report("**Verdict: PROMOTE - beats**") == "PROMOTE"
    assert mc.verdict_from_report("**Verdict: NO-GO (tie)**") == "NO-GO"
    assert mc.verdict_from_report("**Verdict: INCOMPLETE - failed**") == \
        "INCOMPLETE"
    assert mc.verdict_from_report("no verdict") is None


def test_parse_phase_b_done() -> None:
    text = ("step=99975 loss=0.6270 tok/s=103,270 eta=0.0h\n"
            "checkpoint saved: D:\\hwk-models\\context-4k step=100000\n"
            "done: D:\\hwk-models\\context-4k/ step=100000 tokens=3,276,800,000\n")
    phase = mc.parse_phase_b(text)
    assert phase["done"] is True
    assert phase["step"] == 100000
    assert phase["toks"] == "103,270"


def test_fmt_hms_shapes_countdowns() -> None:
    assert mc.fmt_hms(3725) == "1:02:05"
    assert mc.fmt_hms(65) == "01:05"
    assert mc.fmt_hms(0) == "00:00"


def test_big_clock_rows_aligned() -> None:
    rows = mc.big_clock(datetime(2026, 9, 12, 15, 17, 50), blink=False)
    assert len(rows) == 5
    assert all(len(row) == len(rows[0]) for row in rows)
    assert "█" in rows[0]  # digits actually drawn


# ---------------------------------------------------------------------------
# collect_cards: tmp data dir, injected brain probe (never the network)
# ---------------------------------------------------------------------------

def _setup_data(tmp_path: Path, *, train_tqdm: str | None = None,
                train_age_s: float = 30.0) -> Path:
    data = tmp_path / "hwk-data"
    (data / "soup").mkdir(parents=True)
    if train_tqdm is not None:
        live = data / "soup_train_live.log"
        live.write_text(train_tqdm, encoding="utf-8")
        past = time.time() - train_age_s
        import os
        os.utime(live, (past, past))
    (data / "soup" / "resft_pipeline_report.md").write_text(
        "**Verdict: PROMOTE - the tuned model beats the baseline**\n",
        encoding="utf-8")
    (data / "context_training.log").write_text(
        "step=100000 loss=0.7587 tok/s=103,083 eta=0.0h\n"
        "done: D:\\hwk-models\\context-4k/ step=100000\n", encoding="utf-8")
    (data / "caretaker.log").write_text(
        "[23:58:49] === night caretaker going to sleep ===\n",
        encoding="utf-8")
    return data


def _no_probe(_port: int, _timeout: float = 1.5) -> None:
    return None


def test_collect_cards_graduation_promote_is_done(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    verdict = next(c for c in cards if "verdict" in c.title.lower())
    assert verdict.state == "done"
    assert "PROMOTED" in verdict.detail


def test_collect_cards_running_trainer_has_countdown(tmp_path: Path) -> None:
    data = _setup_data(tmp_path, train_tqdm=TQDM_SAMPLE)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    trainer = next(c for c in cards if "re-SFT trainer" in c.title)
    assert trainer.state == "run"
    assert trainer.eta_s == 3600 + 2 * 60 + 28
    assert trainer.frac is not None and 0.76 < trainer.frac < 0.78
    assert "2,927/3,804" in trainer.detail


def test_collect_cards_silent_trainer_is_stalled(tmp_path: Path) -> None:
    data = _setup_data(tmp_path, train_tqdm=TQDM_SAMPLE, train_age_s=3600)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    trainer = next(c for c in cards if "re-SFT trainer" in c.title)
    assert trainer.state == "stall"
    assert "stopped at" in trainer.detail


def test_collect_cards_dead_brain_port_is_a_stall(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    brain = next(c for c in cards if ":20129" in c.title)
    assert brain.state == "stall"
    assert "DOWN" in brain.detail


def test_collect_cards_phase_b_shows_complete(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    phase = next(c for c in cards if "Phase B" in c.title)
    assert phase.state == "done"
    assert "100,000/100,000" in phase.detail


def test_collect_cards_caretaker_sleeping_is_idle(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    care = next(c for c in cards if "caretaker" in c.title.lower())
    assert care.state == "idle"


def test_collect_cards_pipeline_start_marker_says_just_started(
        tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    (data / "soup_pipeline.log").write_text(
        f"[{stamp}] === soup pipeline start ===\n", encoding="utf-8")
    (data / "soup" / "resft_pipeline_report.md").write_text(
        "**Verdict: INCOMPLETE**\n", encoding="utf-8")
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    stage = next(c for c in cards if "pipeline stage" in c.title)
    assert stage.state == "run"
    assert "just started" in stage.detail


# ---------------------------------------------------------------------------
# Rendering: colorful frame, honest countdown, plain mode strips ANSI
# ---------------------------------------------------------------------------

def test_render_contains_clock_cards_and_next_event(tmp_path: Path) -> None:
    data = _setup_data(tmp_path, train_tqdm=TQDM_SAMPLE)
    cards = mc.collect_cards(data, now=time.time(),
                             brain_probe=lambda p, t=1.5: "checkpoint-2900")
    frame = mc.render(cards, datetime(2026, 9, 12, 17, 30, 0), frame=3,
                      width=120)
    assert "MISSION CONTROL" in frame
    assert "NEXT EVENT" in frame and "re-SFT trainer" in frame
    assert "AALI GRADUATED" in frame  # verdict card is done -> celebrate
    assert "\x1b[" in frame  # colored by default


def test_render_plain_has_no_ansi(tmp_path: Path) -> None:
    data = _setup_data(tmp_path, train_tqdm=TQDM_SAMPLE)
    cards = mc.collect_cards(data, now=time.time(),
                             brain_probe=lambda p, t=1.5: "checkpoint-2900")
    frame = mc.render(cards, datetime(2026, 9, 12, 17, 30, 0), frame=3,
                      width=120, plain=True)
    assert "\x1b[" not in frame
    assert "MISSION CONTROL" in frame


def test_render_idle_when_nothing_counts_down(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe)
    frame = mc.render(cards, datetime(2026, 9, 12, 17, 30, 0), frame=0,
                      width=100, plain=True)
    assert "nothing is counting down" in frame


# ---------------------------------------------------------------------------
# GPU card: nvidia-smi parser + state logic (probe always injected in tests)
# ---------------------------------------------------------------------------

RTX_ROW = "NVIDIA GeForce RTX 3070, 9, 7744, 8192, 48"


def test_parse_nvidia_smi_row() -> None:
    info = mc.parse_nvidia_smi(RTX_ROW)
    assert info == {"name": "NVIDIA GeForce RTX 3070", "util_pct": 9,
                    "vram_used_mib": 7744, "vram_total_mib": 8192,
                    "temp_c": 48}


def test_parse_nvidia_smi_name_with_comma() -> None:
    # split from the RIGHT so multi-word/comma GPU names survive
    info = mc.parse_nvidia_smi("Brand, Special GPU, 95, 1000, 2000, 60")
    assert info is not None
    assert info["name"] == "Brand, Special GPU"
    assert info["vram_used_mib"] == 1000 and info["vram_total_mib"] == 2000


def test_parse_nvidia_smi_garbage_is_none() -> None:
    assert mc.parse_nvidia_smi("only, four, parts, here") is None
    assert mc.parse_nvidia_smi("a, b, c, x, y, z") is None  # non-numeric tail


def test_gpu_card_busy_is_run() -> None:
    card = mc.gpu_card({"name": "RTX 3070", "util_pct": 95,
                        "vram_used_mib": 7800, "vram_total_mib": 8192,
                        "temp_c": 71})
    assert card is not None and card.state == "run"
    assert "cooking" in card.detail and "95% util" in card.detail
    assert card.frac is not None and card.frac > 0.9
    assert card.eta_s is None  # the GPU itself never counts down


def test_gpu_card_resident_but_idle_is_wait() -> None:
    # the promoted soup server lives in VRAM while idle - the honest state
    card = mc.gpu_card({"name": "RTX 3070", "util_pct": 0,
                        "vram_used_mib": 7744, "vram_total_mib": 8192,
                        "temp_c": 48})
    assert card is not None and card.state == "wait"
    assert "resident" in card.detail


def test_gpu_card_cold_and_empty_is_idle() -> None:
    card = mc.gpu_card({"name": "RTX 3070", "util_pct": 3,
                        "vram_used_mib": 800, "vram_total_mib": 8192,
                        "temp_c": 40})
    assert card is not None and card.state == "idle"


def test_gpu_card_hot_weather_warning() -> None:
    card = mc.gpu_card({"name": "RTX 3070", "util_pct": 95,
                        "vram_used_mib": 7800, "vram_total_mib": 8192,
                        "temp_c": 90})
    assert card is not None and card.extra.endswith("🥵")


def test_gpu_card_none_info_is_none() -> None:
    assert mc.gpu_card(None) is None  # no nvidia-smi -> no card, never a crash


def test_collect_cards_gpu_card_injected(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)

    def probe() -> dict:
        return {"name": "RTX 3070", "util_pct": 92, "vram_used_mib": 7900,
                "vram_total_mib": 8192, "temp_c": 70}

    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=probe)
    gpu = next(c for c in cards if c.title.startswith("GPU"))
    assert gpu.state == "run" and "RTX 3070" in gpu.title


def test_collect_cards_gpu_probe_none_means_no_card(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=None)
    assert not any(c.title.startswith("GPU") for c in cards)


def test_collect_cards_phase_c_states(tmp_path: Path) -> None:
    """Phase C (Aali's own brain) card: waiting -> running -> done, parsed
    from the chain log + the trainer's step= lines."""
    data = _setup_data(tmp_path)
    # default fixture: no chain log -> idle
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=None)
    card = next(c for c in cards if "Phase C" in c.title)
    assert card.state == "idle"

    # chain waiting for v4
    (data / mc.PHASE_C_CHAIN_LOG).write_text(
        "[2026-09-13 00:19:11] === Phase C chain start ===\n"
        "[2026-09-13 00:19:11] waiting for the v4 verdict\n", encoding="utf-8")
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=None)
    card = next(c for c in cards if "Phase C" in c.title)
    assert card.state == "wait"
    assert "waiting" in card.detail

    # trainer running: step= lines from phase_c_sft.log drive the progress
    (data / mc.PHASE_C_LOG).write_text(
        "step=1400 loss=0.71 tok/s=48,000 eta=0.0h\n", encoding="utf-8")
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=None)
    card = next(c for c in cards if "Phase C" in c.title)
    assert card.state == "run"
    assert card.frac is not None and 0.49 < card.frac < 0.52  # 1400/2800

    # done marker wins over everything
    with (data / mc.PHASE_C_CHAIN_LOG).open("a", encoding="utf-8") as fh:
        fh.write("[2026-09-13 06:40:00] === Phase C chain done ===\n")
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=None)
    card = next(c for c in cards if "Phase C" in c.title)
    assert card.state == "done" and card.frac == 1.0


def test_collect_cards_phase_c_failure_is_a_stall(tmp_path: Path) -> None:
    data = _setup_data(tmp_path)
    (data / mc.PHASE_C_CHAIN_LOG).write_text(
        "[2026-09-13 06:30:00] Phase C SFT FAILED - see phase_c_sft.log\n",
        encoding="utf-8")
    cards = mc.collect_cards(data, now=time.time(), brain_probe=_no_probe,
                             gpu_probe=None)
    card = next(c for c in cards if "Phase C" in c.title)
    assert card.state == "stall"
    assert "FAILED" in card.detail
