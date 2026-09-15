# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for آلي Node — the user-side Aali Cloud daemon.
#
# Same proven pattern as Aali-Desktop.spec (one-file, warm-black icon) but
# the entry point is aali_node/daemon.py's own main() — the SAME code the
# headless `python -m aali_node` serves, so CLI mode keeps working from the
# exe (bash/zsh/PowerShell/CMD), and `--shell` turns on the pywebview
# window + pystray tray exactly like the source run.
#
# Build:  scripts/build_node.bat
# Output: build-desktop/dist/aali-node.exe

import sys

hi = [
    # aali_node modules are a real package (found by pathex), but the lazy
    # imports inside daemon.py/confirm.py/shell.py are invisible to static
    # analysis — pin them:
    "aali_node.activate",       # --activate-update worker
    "aali_node.confirm",        # native ctypes dialog + console fallback
    "aali_node.updater",        # opt-in signed update check (--update-hub)
    "aali_node.shell",          # GUI mode (--shell)
]
if sys.platform == "win32":
    # pywebview's Windows backends (same hiddenimports as Aali-Desktop.spec)
    hi += ["webview", "webview.platforms.winforms",
           "webview.platforms.edgechromium"]
    # pystray's Windows backend is imported lazily by shell.py's tray
    hi += ["pystray._win32"]

a = Analysis(
    ["launch_aali_node.py"],
    # "." resolves aali_node/aali_hub; "file-agent" resolves file_agent
    # (sandbox/protocol/file_tools — bundled so the exe's jail reuses the
    # brain's own tool implementations and _resolve).
    pathex=[".", "file-agent"],
    binaries=[],
    datas=[],
    hiddenimports=hi,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
        "torch", "transformers", "pytest", "flask", "requests",
        "PIL.ImageQt",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="aali-node",
        icon="build-desktop/icon.ico",
        # console=True on purpose: headless CLI mode is the daemon's primary
        # contract (bash/PowerShell/CMD all work through it). GUI users run
        # the exe WITH --shell; pywebview still opens its native window fine
        # from a console-built exe.
        console=True,
        disable_windowed_traceback=False,
        upx=False,
        version="build-desktop/version_info.txt",
    )
else:
    # macOS/Linux: the Node daemon is plain stdlib + websockets; build a
    # console binary so CI/dev machines can smoke the packaged entry point.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="aali-node",
        icon=None,
        console=True,
        upx=False,
    )
