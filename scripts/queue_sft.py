"""Watches D:\\hwk-data\\training.log and automatically launches Stage B SFT
(scripts\\sft_training.bat) once Phase A pretraining reaches its target step
or stops updating (e.g. the user closed the training window early).

Run this in its own console window, separate from resume_training.bat:
    scripts\\queue_sft.bat
It only reads the log file; it does not touch the GPU itself, so it is safe
to leave running alongside pretraining.
"""
import os
import re
import subprocess
import time

LOG = r"D:\hwk-data\training.log"
TARGET_STEP = 89975  # resume_training.bat's --max-steps is 90000
STALE_SECONDS = 600  # no new log line for 10 min -> assume the window closed
POLL_SECONDS = 60


def last_step_and_mtime():
    if not os.path.exists(LOG):
        return None, None
    mtime = os.path.getmtime(LOG)
    step = None
    with open(LOG, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = re.search(r"step=(\d+)", line)
            if match:
                step = int(match.group(1))
    return step, mtime


def main() -> None:
    print("Waiting for Aali pretraining to finish (or stop) before starting SFT...")
    while True:
        step, mtime = last_step_and_mtime()
        now = time.time()
        if step is not None and step >= TARGET_STEP:
            print(f"Pretraining reached step {step} (>= {TARGET_STEP}). Starting SFT.")
            break
        if mtime is not None and (now - mtime) > STALE_SECONDS:
            print(f"training.log unchanged for {STALE_SECONDS}s (last step={step}). Assuming pretraining stopped. Starting SFT.")
            break
        time.sleep(POLL_SECONDS)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bat = os.path.join(root, "scripts", "sft_training.bat")
    subprocess.call([bat], shell=True)


if __name__ == "__main__":
    main()
