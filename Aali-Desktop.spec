# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for آلي Desktop — Windows: one-file windowed exe.
# macOS: run scripts/build_desktop_mac.sh (produces Aali-Desktop.app).

import os
import sys

# Bundle cloudflared (for the one-click share tunnel) when a copy exists.
_cf = None
for cand in (
    "build-desktop/cloudflared.exe",
    "scripts/cloudflared.exe",
    "scripts/cloudflared",  # macOS / linux binary, if placed there
):
    if os.path.isfile(cand):
        _cf = cand
        break
datas = [(_cf, ".")] if _cf else []

hi = ["webview"]
if sys.platform == "win32":
    hi += ["webview.platforms.winforms", "webview.platforms.edgechromium"]
elif sys.platform == "darwin":
    hi += ["webview.platforms.cocoa"]

a = Analysis(
    ["aali_desktop_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hi,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy", "PIL.ImageQt",
        "torch", "transformers", "pytest", "flask",
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
        name="Aali-Desktop",
        icon="build-desktop/icon.ico",
        console=False,
        disable_windowed_traceback=False,
        upx=False,
        version="build-desktop/version_info.txt",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Aali-Desktop",
        icon=None,
        console=False,
        upx=False,
    )
    app = BUNDLE(
        exe,
        name="Aali-Desktop.app",
        info_plist={
            "CFBundleDisplayName": "آلي",
            "CFBundleShortVersionString": "1.0.1",
            "NSHighResolutionCapable": True,
        },
    )
