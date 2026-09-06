"""Watches the training logs and automatically runs the rest of Aali's
pipeline, one stage at a time:

  Phase A (context=1024, resume_training.bat, already running)
    -> extend_context.bat   (continue at context=4096, same weights, RoPE)
    -> sft_training.bat     (tool-calling + instruction SFT on the 4096 model)

Each stage is watched by tailing its own log file for "step=<max-steps>" or,
if that stage's window gets closed early, for the log going quiet for a
while - either is treated as "stage finished, move on". Run this in its own
console window, separate from resume_training.bat; it never touches the
GPU itself, it only reads log files and launches the next .bat when ready.
"""
import os
import re
import subprocess
import time

STALE_SECONDS = 600  # no new log line for 10 min -> assume the window closed
POLL_SECONDS = 60
LOG_GRACE_SECONDS = 180  # time to allow a freshly launched stage's log to appear

PHASE_A = {"name": "Phase A pretraining", "log": r"D:\hwk-data\training.log", "target_step": 89975}
CONTEXT_EXT = {"name": "Context extension (1024 -> 4096)", "log": r"D:\hwk-data\context_training.log", "target_step": 99975, "bat": "extend_context.bat"}
SFT = {"name": "Stage B SFT", "log": r"D:\hwk-data\sft_training.log", "target_step": 102975, "bat": "sft_training.bat"}


def last_step_and_mtime(log_path: str):
    if not os.path.exists(log_path):
        return None, None
    mtime = os.path.getmtime(log_path)
    step = None
    with open(log_path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = re.search(r"step=(\d+)", line)
            if match:
                step = int(match.group(1))
    return step, mtime


def wait_for_stage(stage: dict) -> None:
    print(f"Watching: {stage['name']} ({stage['log']})")
    deadline_for_log_to_appear = time.time() + LOG_GRACE_SECONDS
    while True:
        step, mtime = last_step_and_mtime(stage["log"])
        now = time.time()
        if step is not None and step >= stage["target_step"]:
            print(f"{stage['name']}: reached step {step} (>= {stage['target_step']}). Done.")
            return
        if mtime is not None and (now - mtime) > STALE_SECONDS:
            print(f"{stage['name']}: log unchanged for {STALE_SECONDS}s (last step={step}). Assuming stopped. Moving on.")
            return
        if mtime is None and now > deadline_for_log_to_appear:
            print(f"{stage['name']}: log never appeared. Moving on anyway.")
            return
        time.sleep(POLL_SECONDS)


def launch(root: str, bat_name: str) -> None:
    bat = os.path.join(root, "scripts", bat_name)
    print(f"Launching {bat_name} ...")
    subprocess.Popen([bat], shell=True, cwd=root)
    time.sleep(5)


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    wait_for_stage(PHASE_A)

    launch(root, CONTEXT_EXT["bat"])
    wait_for_stage(CONTEXT_EXT)

    launch(root, SFT["bat"])
    wait_for_stage(SFT)

    print("Pipeline watcher: all stages finished.")


if __name__ == "__main__":
    main()
