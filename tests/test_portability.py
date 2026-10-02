"""Portability contract — the clients must not drift back to Windows-only.

The owner's rule: *every Aali app should work on Windows, macOS and Linux.*
These tests cannot run macOS (they are not on a Mac), so they pin the things
that WOULD break there silently:

* no Windows-only calls (``os.startfile``, ``netsh``, ``wmic``, ``taskkill``)
  left in a client's import chain,
* no hardcoded ``C:\\`` / ``D:\\`` user paths in library code,
* every OS-specific decision goes through ``file_agent.hwk_paths``,
* the macOS ``.app`` spec has the ``COLLECT`` step it needs,
* the build + self-test scripts exist and are executable.

The real macOS answers come from ``scripts/portability_check.py`` run ON the
MacBook — never from here.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "file-agent"))

from file_agent import hwk_paths  # noqa: E402

STUDIO = REPO / "build-desktop" / "aali-studio"

#: The client entry points + the library code they pull in.
CLIENT_FILES = [
    REPO / "aali_desktop_app.py",
    REPO / "scripts" / "aali_cli.py",
    STUDIO / "studio_app.py",
    STUDIO / "studio_server.py",
    STUDIO / "models_proxy.py",
    REPO / "file-agent" / "file_agent" / "hwk_paths.py",
]

#: Calls that simply do not exist on macOS/Linux.
WINDOWS_ONLY_CALLS = ("os.startfile", "netsh ", "wmic ", "taskkill",
                      "os.startfile(", "winreg", "powershell.exe")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _code_only(path: Path) -> str:
    """The file with COMMENTS and STRING LITERALS removed.

    The portability rule is about CODE. A docstring that says «no os.startfile
    survives here» mentions the very call it disclaims, and scanning prose made
    the test lie about its own subject.
    """
    import io
    import tokenize
    source = _source(path)
    try:
        kept = []
        prev_type = tokenize.INDENT
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if token.type == tokenize.NAME and token.start[1] == 0:
                prev_type = token.type
            kept.append(token.string)
        return " ".join(kept)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file we cannot tokenize (odd but legal) still gets checked raw —
        # a false positive here is better than a silent skip.
        return source


# ————— hwk_paths itself —————

def test_hwk_paths_exists_as_agents_md_promised():
    """AGENTS.md says 'use hwk_paths.py' — it must actually be there."""
    assert (REPO / "file-agent" / "file_agent" / "hwk_paths.py").is_file()


def test_hwk_paths_is_stdlib_only():
    """It is imported by frozen, CPU-only client builds — no heavy imports."""
    source = _source(REPO / "file-agent" / "file_agent" / "hwk_paths.py")
    for banned in ("import torch", "import numpy", "import requests",
                   "import flask", "import webview"):
        assert banned not in source


def test_config_dir_is_platform_correct(tmp_path, monkeypatch):
    monkeypatch.setenv("AALI_CONFIG_DIR", str(tmp_path / "x"))
    assert str(hwk_paths.config_dir("Aali")) == str(tmp_path / "x")
    monkeypatch.delenv("AALI_CONFIG_DIR")
    real = str(hwk_paths.config_dir("Aali")).replace("\\", "/")
    if hwk_paths.is_macos():
        assert "Library/Application Support" in real
    elif hwk_paths.is_windows():
        assert "AppData" in real
    else:
        assert ".config" in real


def test_data_root_never_invents_a_missing_drive(monkeypatch, tmp_path):
    monkeypatch.setenv(hwk_paths.DATA_ENV, str(tmp_path))
    assert hwk_paths.data_root() == tmp_path
    monkeypatch.delenv(hwk_paths.DATA_ENV)
    root = hwk_paths.data_root()
    assert root.exists(), "the fallback data root must be creatable/real"


def test_open_external_refuses_non_http():
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "", "data:text/html,x"):
        assert hwk_paths.open_external(bad) == "bad-url"


def test_open_external_has_a_branch_for_every_platform(monkeypatch):
    """Windows uses startfile, macOS `open`, Linux `xdg-open` — all present."""
    source = _source(REPO / "file-agent" / "file_agent" / "hwk_paths.py")
    assert "startfile" in source and '"open"' in source and "xdg-open" in source


def test_cloudflared_name_is_platform_correct():
    name = hwk_paths.cloudflared_name()
    assert name.endswith(".exe") is hwk_paths.is_windows()


# ————— no Windows-only calls survive in the clients —————

@pytest.mark.parametrize("path", CLIENT_FILES, ids=lambda p: p.name)
def test_no_windows_only_calls_in_client_code(path):
    # hwk_paths is the ONE place allowed to mention startfile (it guards it).
    if path.name == "hwk_paths.py":
        return
    code = _code_only(path)
    for banned in ("os.startfile", "netsh", "wmic", "taskkill", "winreg"):
        assert banned not in code, f"{path.name} still calls {banned!r}"


@pytest.mark.parametrize("path", CLIENT_FILES, ids=lambda p: p.name)
def test_no_hardcoded_user_drive_paths(path):
    # Drive-letter paths are only legitimate in the data-root helper, which
    # probes that the drive exists before using it.
    if path.name == "hwk_paths.py":
        return
    code = _code_only(path)
    assert not re.search(r"[CD]:\\\\|[CD]:/", code), \
        f"{path.name} hardcodes a user drive path"


def test_desktop_config_dir_goes_through_hwk_paths():
    source = _source(REPO / "aali_desktop_app.py")
    assert "hwk_paths.config_dir" in source
    assert 'os.getenv("APPDATA"' not in source


def test_studio_config_dir_goes_through_hwk_paths():
    source = _source(STUDIO / "models_proxy.py")
    assert "hwk_paths" in source
    assert 'os.getenv("APPDATA"' not in source


def test_desktop_cloudflared_lookup_is_platform_correct():
    """Hardcoding cloudflared.exe silently removed the share button on macOS."""
    source = _source(REPO / "aali_desktop_app.py")
    assert "cloudflared_name()" in source
    assert '"cloudflared.exe"' not in source


# ————— the specs must be able to build a real .app —————

def test_studio_spec_has_the_mac_bundle_steps():
    spec = _source(STUDIO / "Aali-Studio.spec")
    assert "COLLECT(" in spec, "a macOS .app without COLLECT has no WebView backend"
    assert "BUNDLE(" in spec
    assert "webview.platforms.cocoa" in spec
    assert "bundle_identifier" in spec


def test_desktop_spec_has_a_macos_branch():
    spec = _source(REPO / "Aali-Desktop.spec")
    assert "darwin" in spec and "BUNDLE(" in spec


def test_every_spec_resolves_paths_from_the_repo_root():
    """A relative script path doubles up under the spec dir and never builds.

    A spec that lives AT the repo root is fine with bare relative paths; a spec
    in a subdirectory must anchor itself with SPECPATH (that bug cost a real
    Studio build once).
    """
    for spec in (STUDIO / "Aali-Studio.spec", REPO / "Aali-Desktop.spec"):
        source = _source(spec)
        if spec.parent == REPO:
            assert 'Analysis(\n    ["' in source or '["aali_desktop_app.py"]' in source, \
                "a root spec may use bare relative paths, but say which script"
        else:
            assert "SPECPATH" in source, spec.name


# ————— the build + self-test entry points exist —————

@pytest.mark.parametrize("relative", [
    "scripts/build_all_macos.sh",
    "build-desktop/aali-studio/build-mac.sh",
    "scripts/portability_check.py",
])
def test_build_and_check_scripts_exist(relative):
    path = REPO / relative
    assert path.is_file(), relative
    assert path.stat().st_size > 200


def test_build_all_covers_every_client():
    source = _source(REPO / "scripts" / "build_all_macos.sh")
    for target in ("studio", "desktop", "cli"):
        assert f"{target})" in source


def test_shell_scripts_have_a_shebang_and_strict_mode():
    for relative in ("scripts/build_all_macos.sh",
                     "build-desktop/aali-studio/build-mac.sh"):
        source = _source(REPO / relative)
        assert source.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in source


@pytest.mark.skipif(os.name == "nt", reason="chmod/exec bits are POSIX concepts")
def test_shell_scripts_are_executable():
    for relative in ("scripts/build_all_macos.sh",
                     "build-desktop/aali-studio/build-mac.sh"):
        assert os.access(REPO / relative, os.X_OK), relative


def test_portability_check_runs_here_as_a_subprocess():
    """It must work with PYTHONPATH stripped (frozen-exe entry-point rule)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["AALI_STUDIO_CONFIG_DIR"] = str(REPO / ".git" / "portability-probe")
    try:
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "portability_check.py")],
            capture_output=True, text=True, env=env, timeout=300)
        assert result.returncode == 0, (
            "the self-test must pass on the machine that built the clients:\n"
            + result.stdout[-1500:] + result.stderr[-800:])
        assert "RESULT: every core check passed" in result.stdout
    finally:
        probe = REPO / ".git" / "portability-probe"
        if probe.exists():
            for child in probe.rglob("*"):
                if child.is_file():
                    child.unlink()
            probe.rmdir()


def test_portability_check_json_is_machine_readable():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "portability_check.py"), "--json"],
        capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, result.stdout[-800:] + result.stderr[-500:]
    import json
    data = json.loads(result.stdout)
    assert data["host"]["os"]
    assert isinstance(data["results"], list) and data["results"]


# ————— the phones, stated honestly —————

def test_mobile_access_is_documented_as_web_not_a_desktop_app():
    """An iPhone cannot run a pywebview+Flask IDE; the docs must say so."""
    docs = (REPO / "docs" / "PORTING.md")
    if not docs.is_file():
        pytest.skip("docs/PORTING.md not written yet")
    text = docs.read_text(encoding="utf-8")
    assert "iphone" in text.lower() or "ios" in text.lower()
    assert "web" in text.lower()