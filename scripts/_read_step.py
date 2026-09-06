"""Print (saved trainer step + extra) from a trainer-state.pt checkpoint.

Used by sft_now.bat to compute --max-steps without embedding single quotes
in a batch `for /f` command (cmd.exe's for /f terminates its captured
command at the first single quote, which was silently truncating the old
inline `python -c '...'` one-liner and left MAXSTEPS stuck at 0).
"""
import sys

import torch

path = sys.argv[1]
extra = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
state = torch.load(path, map_location="cpu", weights_only=False)
print(int(state.get("step", 0)) + extra)
