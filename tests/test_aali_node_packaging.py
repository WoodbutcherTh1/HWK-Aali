"""Packaging contract for the آلي Node exe (Aali Cloud STEP 5).

The exe is built by scripts/build_node.bat from aali-node.spec with the
entry shim launch_aali_node.py. These tests pin the CONTRACTS that silently
broke once already (and would again):

- the spec's pathex MUST contain "file-agent" — sandbox.py imports
  file_agent.protocol at module level and PyInstaller's static analysis
  only follows it when the directory is on the analysis path. Without it
  the exe builds green and dies at launch (ModuleNotFoundError) — exactly
  what the first build did.
- the spec's hiddenimports MUST pin the lazy imports (activate/confirm/
  updater/shell) — static analysis cannot see them.
- the build venv must install websockets/pystray/cryptography — runtime
  deps of the daemon/shell and the PRODUCTION update-signature path.
- the smoke runbook must keep its --frozen-exe mode so the packaged exe
  stays provable end-to-end (real Hub, real dispatch).

No exe is built here (PyInstaller in the test venv would be a new heavy
dep — the rule). The exe proof is the operator smoke run:
    .venv-hub/Scripts/python.exe scripts/smoke_aali_node.py \
        --frozen-exe build-desktop/dist/aali-node.exe
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "aali-node.spec"
LAUNCH = ROOT / "launch_aali_node.py"
BUILD_BAT = ROOT / "scripts" / "build_node.bat"
SMOKE = ROOT / "scripts" / "smoke_aali_node.py"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing packaging file: {path}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# aali-node.spec
# --------------------------------------------------------------------------

def test_spec_pathex_includes_file_agent() -> None:
    """file-agent/ on the analysis path — without it the exe cannot import
    file_agent.protocol (sandbox) and dies at launch. Regression."""
    spec = _read(SPEC)
    m = re.search(r"pathex\s*=\s*\[([^\]]*)\]", spec)
    assert m, "aali-node.spec has no pathex list"
    assert any("file-agent" in part for part in m.group(1).split(",")), (
        "aali-node.spec pathex lost 'file-agent' — the packaged exe will "
        "build but fail at launch with ModuleNotFoundError: file_agent")


def test_spec_entry_point_is_launch_shim() -> None:
    spec = _read(SPEC)
    assert '"launch_aali_node.py"' in spec, (
        "aali-node.spec must analyze launch_aali_node.py (the shim wires "
        "file-agent/ into sys.path before importing the daemon)")
    assert SPEC.parent is not None


def test_spec_hiddenimports_pin_lazy_node_modules() -> None:
    """daemon/confirm/shell lazy-import these; static analysis is blind to
    lazy imports — a missing pin means a runtime feature vanishes from the
    exe while every test stays green. (aali_node.daemon itself needs no pin:
    the shim imports it directly, so analysis follows it.)"""
    spec = _read(SPEC)
    for mod in ("aali_node.activate", "aali_node.confirm",
                "aali_node.updater", "aali_node.shell"):
        assert f'"{mod}"' in spec, f"spec hiddenimports lost {mod}"


def test_spec_excludes_requests() -> None:
    """The Node must never need `requests` (hub venv rule; redaction was
    extracted to file_agent/redaction.py for exactly this). If the spec
    drops the exclude, a future import drags the whole requests tree in."""
    spec = _read(SPEC)
    assert "requests" in spec.split("excludes=")[1].split("]")[0]


# --------------------------------------------------------------------------
# launch_aali_node.py
# --------------------------------------------------------------------------

def test_launch_shim_compiles_and_wires_paths() -> None:
    import py_compile

    py_compile.compile(str(LAUNCH), doraise=True)
    src = _read(LAUNCH)
    assert 'str(_HERE / "file-agent")' in src, (
        "launch shim must add file-agent/ to sys.path at runtime too")
    assert "from aali_node.daemon import main" in src


# --------------------------------------------------------------------------
# scripts/build_node.bat
# --------------------------------------------------------------------------

def test_build_bat_installs_node_runtime_deps() -> None:
    """websockets (daemon transport) + pystray (tray) + cryptography
    (production Ed25519 update verification) must be in the BUILD venv or
    the exe ships without them and fails closed on every real update."""
    bat = _read(BUILD_BAT)
    for dep in ("pyinstaller", "pywebview", "pillow", "pystray",
                "websockets", "cryptography"):
        assert dep in bat, f"build_node.bat lost runtime dep {dep}"
    assert "aali-node.spec" in bat, "build_node.bat must build the spec"


# --------------------------------------------------------------------------
# scripts/smoke_aali_node.py — the frozen-exe proof runbook
# --------------------------------------------------------------------------

def test_smoke_runbook_has_frozen_exe_mode() -> None:
    smoke = _read(SMOKE)
    assert "--frozen-exe" in smoke, (
        "smoke runbook lost --frozen-exe — the packaged exe loses its "
        "end-to-end proof (real hub + real dispatch)")


def test_smoke_frozen_exe_rejects_missing_path() -> None:
    """A typo'd exe path must exit 2 with a message, not fall back to the
    source-mode smoke (which would pass while proving nothing about the
    exe)."""
    r = subprocess.run(
        [sys.executable, str(SMOKE), "--frozen-exe", "no/such/exe.exe",
         "--keep"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}"
    assert "no such file" in (r.stdout + r.stderr).lower()
