"""Compare disk free-space readings from three sources.

`pc_agent /api/resources` reports disk space with shutil.disk_usage, and a
training gate would trust it, so a disagreement between Python and
Get-Volume has to be understood rather than assumed away.
"""
import ctypes
import shutil
import subprocess
import sys

free_bytes_per_volume = ctypes.c_ulonglong(0)
total_bytes = ctypes.c_ulonglong(0)
total_free = ctypes.c_ulonglong(0)
kernel32 = ctypes.windll.kernel32


def win_api(letter: str) -> tuple[float, float]:
    # Two defects lived here before 2026-10-06, both making the call fail
    # (ok=0 -> nan), which a reader could mistake for a real disagreement
    # between sources:
    #   1. GetDiskFreeSpaceExW is the WIDE-char API - it takes a str, never
    #      the bytes the old code built (c_char_p).
    #   2. main() passes letter as "C:" (colon included) and the old code
    #      appended another ":\\" -> root "C::\\" = ERROR_INVALID_NAME (123).
    # rstrip normalizes "C" and "C:" to the same valid root.
    root = f"{letter.rstrip(':')}:\\"
    ok = kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(root),
        ctypes.byref(free_bytes_per_volume),
        ctypes.byref(total_bytes),
        ctypes.byref(total_free),
    )
    if not ok:
        return float("nan"), float("nan")
    return free_bytes_per_volume.value / 2 ** 30, total_bytes.value / 2 ** 30


def main() -> int:
    print(f"{'drive':6} {'shutil GB':>12} {'winapi GB':>12} {'agree':>7}")
    for letter in ("C:", "D:", "X:"):
        py_free = shutil.disk_usage(letter + "/").free / 2 ** 30
        api_free, api_total = win_api(letter)
        agree = "YES" if abs(py_free - api_free) < 1.0 else "NO  <<<<"
        print(f"{letter:6} {py_free:12.1f} {api_free:12.1f} {agree:>7}")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Volume | Where-Object DriveLetter | "
         "ForEach-Object { \"$($_.DriveLetter) $([math]::Round($_.SizeRemaining/1GB,1))\" }"],
        capture_output=True, text=True, timeout=60)
    print("--- Get-Volume (authoritative) ---")
    print((out.stdout or out.stderr).strip())
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())