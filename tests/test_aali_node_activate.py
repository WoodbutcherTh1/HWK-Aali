"""Tests for aali_node.activate — the staged-update swap (STEP 5's last gap).

All headless and pure-filesystem: real trees under tmp_path, restart paths
observed through monkeypatched spawners (no real detached processes in
pytest), rollback proven by making the copy step fail mid-swap.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aali_node import activate as ACT
from aali_node import daemon as ND


def _make_install(root: Path, version: str, *,
                  files: dict[str, str] | None = None) -> Path:
    """A minimal but structurally valid Node install tree."""
    init = root / "aali_node" / "__init__.py"
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text(f'"""pkg"""\n__version__ = "{version}"\n', encoding="utf-8")
    for name, content in (files or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# version markers
# ---------------------------------------------------------------------------
def test_read_version_marker(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    assert ACT.read_version_marker(init) == "1.2.3"
    init.write_text('x = 1\n', encoding="utf-8")
    with pytest.raises(ACT.ActivationError):
        ACT.read_version_marker(init)


def test_staged_version_refuses_non_install(tmp_path: Path) -> None:
    with pytest.raises(ACT.ActivationError):
        ACT.staged_version(tmp_path)  # no aali_node/__init__.py at all


# ---------------------------------------------------------------------------
# the swap
# ---------------------------------------------------------------------------
def test_activate_swaps_tree_and_parks_old_version(tmp_path: Path) -> None:
    install = _make_install(tmp_path / "install", "0.1.0",
                            files={"old_only.txt": "old"})
    staged = _make_install(install / "staged" / "0.2.0", "0.2.0",
                           files={"new_only.txt": "new"})

    version = ACT.activate_update(staged, install)

    assert version == "0.2.0"
    assert ACT.install_version(install) == "0.2.0"
    assert (install / "new_only.txt").read_text() == "new"
    # MOVE-not-merge proof: the old tree's own file is gone from the root
    assert not (install / "old_only.txt").exists()
    # the old install is parked next to the install root for manual rollback
    assert (install.parent / ".aali_prev" / "old_only.txt").is_file()
    assert ACT.install_version(install.parent / ".aali_prev") == "0.1.0"
    # staging area survives (updater-owned)
    assert (install / "staged" / "0.2.0" / "aali_node").is_dir()


def test_activate_refuses_same_version(tmp_path: Path) -> None:
    install = _make_install(tmp_path / "install", "0.2.0")
    staged = _make_install(install / "staged" / "0.2.0", "0.2.0")
    with pytest.raises(ACT.ActivationError, match="equals the running"):
        ACT.activate_update(staged, install)


def test_activate_refuses_staged_equal_to_root(tmp_path: Path) -> None:
    install = _make_install(tmp_path / "install", "0.1.0")
    with pytest.raises(ACT.ActivationError, match="IS the install root"):
        ACT.activate_update(install, install)


def test_activate_rolls_back_on_mid_copy_failure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = _make_install(tmp_path / "install", "0.1.0",
                            files={"old_only.txt": "old"})
    staged = _make_install(install / "staged" / "0.2.0", "0.2.0",
                           files={"zzz.txt": "z", "extra/data.txt": "d"})

    real_copy = ACT._copy_tree

    def exploding_copy(src: Path, dst: Path) -> None:
        if src.name == "extra":  # fail on a DIRECTORY copy mid-swap
            raise OSError("disk exploded mid-swap")
        real_copy(src, dst)

    monkeypatch.setattr(ACT, "_copy_tree", exploding_copy)

    with pytest.raises(ACT.ActivationError, match="rolled back"):
        ACT.activate_update(staged, install)

    # the pre-swap state is EXACTLY back
    assert ACT.install_version(install) == "0.1.0"
    assert (install / "old_only.txt").read_text() == "old"
    assert not (install / "extra").exists()        # partial copy removed
    assert not (install / "zzz.txt").exists()
    # the parked backup was moved back, so .aali_prev holds no leftovers
    assert not (install.parent / ".aali_prev" / "aali_node").exists()


def test_activate_restart_goes_through_spawner(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = _make_install(tmp_path / "install", "0.1.0")
    staged = _make_install(install / "staged" / "0.2.0", "0.2.0")

    seen: dict = {}

    def fake_spawn(root: Path, args: list[str], env) -> None:
        seen["root"] = root
        seen["args"] = args

    monkeypatch.setattr(ACT, "_spawn_detached", fake_spawn)
    ACT.activate_update(staged, install, restart=True,
                        restart_args=["--hub", "ws://x"])
    assert seen["root"] == install.resolve()
    assert seen["args"] == ["--hub", "ws://x"]


# ---------------------------------------------------------------------------
# detached worker (parent side + CLI body)
# ---------------------------------------------------------------------------
def test_spawn_detached_activation_launches_worker(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aali_node import activate as act_module
    import subprocess as sp

    install = _make_install(tmp_path / "install", "0.1.0")
    staged = _make_install(install / "staged" / "0.2.0", "0.2.0")

    recorded: dict = {}
    real_popen = act_module.subprocess.Popen

    def fake_popen(cmd, **kwargs):
        recorded["cmd"] = cmd
        return real_popen(["cmd", "/c", "exit 0"], **kwargs) \
            if False else object()

    monkeypatch.setattr(act_module.subprocess, "Popen", fake_popen)
    assert ACT.spawn_detached_activation(staged, install,
                                         restart_args=["--hub", "x"]) is True
    assert recorded["cmd"][0].endswith(("python.exe", "python"))
    assert "-c" in recorded["cmd"]
    assert "run_activation_cli" in recorded["cmd"][2]


def test_run_activation_cli_logs_success_and_failure(tmp_path: Path) -> None:
    install = _make_install(tmp_path / "install", "0.1.0")
    staged = _make_install(install / "staged" / "0.2.0", "0.2.0")

    code = ACT.run_activation_cli(staged, install, restart_args=[])
    assert code == 0
    log = (tmp_path / "activate_update.log").read_text(encoding="utf-8")
    assert "activated 0.2.0" in log

    bad = tmp_path / "not-an-install"
    bad.mkdir()
    code = ACT.run_activation_cli(bad, install, restart_args=[])
    assert code == 1
    log = (tmp_path / "activate_update.log").read_text(encoding="utf-8")
    assert "ACTIVATION FAILED" in log


# ---------------------------------------------------------------------------
# daemon CLI flag (spawn-free branches only)
# ---------------------------------------------------------------------------
def test_cli_activate_refuses_when_nothing_staged(tmp_path: Path) -> None:
    ws = tmp_path / "inst" / "ws"
    ws.mkdir(parents=True)
    rc = ND.main(["--activate-update", "--token", "t", "--hub", "ws://x",
                  "--workspace", str(ws)])
    assert rc == 2  # nothing staged → honest refusal, no worker launched


def test_cli_activate_refuses_when_staged_is_garbage(tmp_path: Path) -> None:
    inst = tmp_path / "inst"
    ws = inst / "ws"
    ws.mkdir(parents=True)
    (inst / "staged" / "garbage-name").mkdir(parents=True)
    rc = ND.main(["--activate-update", "--token", "t", "--hub", "ws://x",
                  "--workspace", str(ws)])
    assert rc == 2  # staged dir fails the structural proof
