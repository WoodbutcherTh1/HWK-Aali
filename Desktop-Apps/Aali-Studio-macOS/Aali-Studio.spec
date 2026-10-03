# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for آلي ستوديو — Aali Studio.
#
#   Windows : build.bat                       -> build-desktop\dist\Aali-Studio.exe
#   macOS   : build-mac.sh                    -> build-desktop/dist/Aali-Studio.app
#   Linux   : build-mac.sh (same path, no BUNDLE) -> dist/Aali-Studio
#
# Every path resolves from the REPO ROOT (SPECPATH/../..): PyInstaller
# resolves a spec's script path relative to the SPEC directory, so a relative
# 'build-desktop/...' path doubles up and never builds.
# The exe bundles the web UI (web/) and keeps file-agent/ on pathex so both the
# sandbox (file_agent.file_tools) and the platform paths
# (file_agent.hwk_paths) import: without them the frozen IDE would have no file
# sandbox and would scatter config in the home directory.

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
STUDIO = os.path.join(ROOT, "build-desktop", "aali-studio")


def icon_or_none(*relative):
    """The first icon asset that actually exists, else None.

    PyInstaller raises FileNotFoundError on a missing icon — it does not fall
    back to a default. That killed the owner's first macOS build at the very
    last step (BUNDLE) over a purely cosmetic asset, after 14 seconds of
    successful analysis and collection. A missing icon must cost an icon, not
    the build.
    """
    for name in relative:
        path = os.path.join(ROOT, "build-desktop", name)
        if os.path.isfile(path):
            return path
        print("[spec] icon %s not found — building without it" % name)
    return None


ICNS = icon_or_none("icon.icns")
ICO = icon_or_none("icon.ico")

datas = [(os.path.join(STUDIO, "web"), "web")]

hi = ["webview", "models_proxy", "studio_server",
      # The auto-update layer: imported by name from build-desktop/aali-studio
      # and from scripts/, so neither PyInstaller's static analysis nor the
      # frozen sys.path can find it on its own. Without these the frozen IDE
      # builds fine and then refuses every update with an ImportError.
      "updater", "update_ui", "version", "shared", "shared.updater"]
if sys.platform == "win32":
    hi += ["webview.platforms.winforms", "webview.platforms.edgechromium"]
elif sys.platform == "darwin":
    # The macOS backend is a compiled .dylib that lives in webview/lib — it must
    # be COLLECTed, not left out (a window that never appears is the classic
    # macOS packaging failure).
    hi += ["webview.platforms.cocoa", "objc"]

a = Analysis(
    [os.path.join(STUDIO, "studio_app.py")],
    pathex=[ROOT, STUDIO, os.path.join(ROOT, "file-agent"),
            os.path.join(ROOT, "scripts")],
    binaries=[],
    datas=datas,
    hiddenimports=hi,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy", "torch",
        "transformers", "pytest", "PIL.ImageQt",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="Aali-Studio",
        icon=ICO,
        console=False,
        disable_windowed_traceback=False,
        upx=False,
        version=os.path.join(ROOT, "build-desktop", "version_info.txt"),
    )
elif sys.platform == "darwin":
    # macOS app bundle: EXE (thin) -> COLLECT (the .dylibs and resources) ->
    # BUNDLE (.app). COLLECT IS NOT OPTIONAL: without it the .app has no
    # WebView backend and the window silently never opens.
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="Aali-Studio",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=ICNS,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, name="Aali-Studio",
    )
    app = BUNDLE(
        coll,
        name="Aali-Studio.app",
        icon=ICNS,
        bundle_identifier="org.hwk.aalistudio",
        info_plist={
            "CFBundleDisplayName": "آلي ستوديو",
            "CFBundleName": "Aali Studio",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            # The updater launches the freshly activated payload with `open`,
            # and macOS asks once per app. Say WHY, in Arabic, or the owner
            # clicks Deny and the restart silently never happens.
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.13",
            # The IDE binds 127.0.0.1 and opens no files the owner did not ask
            # for, but it does spawn a terminal — be explicit about both.
            "NSAppleEventsUsageDescription":
                "آلي ستوديو يشغّل أوامر الطرفية التي تطلبها داخل مجلد المشروع فقط.",
        },
    )
else:
    # Linux: a plain binary (WebKit2GTK must be installed on the host).
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="Aali-Studio",
        console=False,
        disable_windowed_traceback=False,
        upx=False,
    )