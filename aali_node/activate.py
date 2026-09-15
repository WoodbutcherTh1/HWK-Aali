"""Aali Node update ACTIVATION — swap a staged, verified update into place.

The updater (aali_node/updater.py) only STAGES verified artifacts under
``<install_root>/staged/<version>/``; this module owns the explicit,
separately-invoked swap:

    python -m aali_node --activate-update            # daemon CLI flag

Contract (deliberately boring, because it rewrites the running install):
- the staged directory must prove its identity FIRST: a real
  ``aali_node/__init__.py`` with a strict dotted-semver ``__version__``;
- the CURRENT install proves itself the same way — a temp/staging dir with
  no live install is refused (nothing to swap into, nothing to roll back to);
- the backup lives at ``<install_root.parent>/.aali_prev`` and is LEFT BEHIND
  on success (manual rollback = copy it back; no auto-undo of a good update);
- any failure mid-copy rolls the tree back to its pre-swap state — a half
  updated Node is never left on disk;
- restart is opt-in and DETACHED (scripts.launch_detached flags): the
  activating process exits, the new Node outlives it.

Trust model: the signature + sha256 verification happened at STAGING time
(updater.py, fail-closed). Activation re-proves structure and version but
not the Hub's signature — the same split every OS package manager makes
between download-verify and install.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

__all__ = ["ActivationError", "staged_version", "install_version",
           "activate_update", "spawn_activation", "read_version_marker"]

_BACKUP_DIRNAME = ".aali_prev"
_MARKER_VERSION_RE = re.compile(
    r"__version__\s*=\s*[\"']([0-9]+(?:\.[0-9]+)*)[\"']")


class ActivationError(Exception):
    """Expected activation failure (bad staging, no install, copy error)."""


def read_version_marker(init_py: Path) -> str:
    """Extract the strict dotted version from an __init__.py, or raise."""
    try:
        text = init_py.read_text(encoding="utf-8")
    except OSError as exc:
        raise ActivationError(
            f"cannot read {init_py.name}: {exc}") from exc
    match = _MARKER_VERSION_RE.search(text)
    if match is None:
        raise ActivationError(
            f"{init_py} has no strict dotted __version__ marker")
    return match.group(1)


def staged_version(staged_dir: str | Path) -> str:
    """The version a staged directory claims (structural proof)."""
    root = Path(staged_dir)
    marker = root / "aali_node" / "__init__.py"
    if not marker.is_file():
        raise ActivationError(
            f"staged directory is not a Node install (missing "
            f"aali_node/__init__.py): {root}")
    return read_version_marker(marker)


def install_version(install_root: str | Path) -> str:
    """The version the CURRENT install claims (must exist to be swappable)."""
    return staged_version(install_root)  # same structural proof


def _copy_tree(src: Path, dst: Path) -> None:
    """Recursive copy that merges into an existing dst (shutil won't)."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)


def activate_update(staged_dir: str | Path, install_root: str | Path,
                    *, restart: bool = False,
                    restart_args: list[str] | None = None,
                    env: dict[str, str] | None = None) -> str:
    """Swap the staged update into install_root; return the new version.

    Steps: prove staged → prove current → move the old tree (except
    ``staged/``) into the backup → copy the staged tree in (except
    ``staged/``) → re-prove the copy. A MOVE, not a merge, so files the
    new version dropped cannot linger. Any failure restores the pre-swap
    state exactly; on success the backup stays for manual rollback.
    """
    from aali_node.updater import parse_version

    staged = Path(staged_dir).resolve()
    root = Path(install_root).resolve()
    if staged == root:
        raise ActivationError("staged directory IS the install root")
    new_version = staged_version(staged)
    if parse_version(new_version) is None:
        raise ActivationError(f"staged version unparsable: {new_version!r}")
    old_version = install_version(root)  # refuses temp dirs with no install
    if old_version == new_version:
        raise ActivationError(
            f"staged version {new_version} equals the running install")

    backup = root.parent / _BACKUP_DIRNAME
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True)

    def _clear_root() -> None:
        for item in root.iterdir():
            if item.name == "staged":  # staging area is update-owned
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    moved_names: list[str] = []
    try:
        # 1) park the running install in the backup (same volume → rename)
        for item in list(root.iterdir()):
            if item.name == "staged":
                continue
            shutil.move(str(item), str(backup / item.name))
            moved_names.append(item.name)
        # 2) copy the staged tree into place (files AND dirs)
        for item in staged.iterdir():
            if item.name == "staged":
                continue
            target = root / item.name
            if item.is_dir():
                _copy_tree(item, target)
            else:
                shutil.copy2(item, target)
        # 3) prove the result before declaring success
        copied_version = install_version(root)
        if copied_version != new_version:
            raise ActivationError(
                f"post-copy proof failed: expected {new_version}, "
                f"found {copied_version!r}")
    except Exception as exc:
        # roll back: drop the half-copied tree, restore what we parked
        _clear_root()
        for name in moved_names:
            src = backup / name
            if src.is_dir():
                shutil.move(str(src), str(root / name))
            elif src.is_file():
                shutil.copy2(src, root / name)
        raise ActivationError(f"swap failed, rolled back: {exc}") from exc

    if restart:
        _spawn_detached(root, restart_args or [], env)
    return new_version


def _spawn_detached(install_root: Path, daemon_args: list[str],
                    env: dict[str, str] | None) -> None:
    """Relaunch the Node from the NEW install, detached from this console."""
    creationflags = 0
    if sys.platform == "win32":
        try:
            from scripts.launch_detached import detached_creationflags
            creationflags = detached_creationflags()
        except Exception:
            creationflags = 0x00000008 | 0x00000200  # DETACHED | NEW_CONSOLE
    if getattr(sys, "frozen", False):
        # PyInstaller build: sys.executable IS the node exe
        cmd = [sys.executable, *daemon_args]
    else:
        cmd = [sys.executable, "-m", "aali_node", *daemon_args]
    subprocess.Popen(
        cmd, cwd=str(install_root), env=env,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)


def spawn_activation(staged_dir: str | Path, install_root: str | Path,
                     *, restart_args: list[str] | None = None,
                     env: dict[str, str] | None = None,
                     log_file: str | Path | None = None) -> int:
    """Run the swap from a DETACHED worker and return its wait() exit code.

    Called via ``--activate-update``: the daemon (which is ABOUT TO BE
    REPLACED) cannot swap its own files on Windows — the running exe locks
    them. It launches a short-lived detached python that performs the swap
    (and optionally starts the new Node), appends a line to ``log_file``,
    and exits. The caller then exits normally.
    """
    import time

    log_path = Path(log_file) if log_file else \
        Path(install_root).parent / "activate_update.log"
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        version = activate_update(staged_dir, install_root,
                                  restart=bool(restart_args),
                                  restart_args=restart_args, env=env)
        line = (f"{started} activated {version} from {staged_dir} "
                f"restart={'yes' if restart_args else 'no'}\n")
        code = 0
    except ActivationError as exc:
        line = f"{started} ACTIVATION FAILED: {exc}\n"
        code = 1
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    return code


def run_activation_cli(staged_dir: str | Path, install_root: str | Path,
                       *, restart_args: list[str] | None = None,
                       env: dict[str, str] | None = None) -> int:
    """CLI wrapper: detached worker body (swap + optional restart + log)."""
    return spawn_activation(staged_dir, install_root,
                            restart_args=restart_args, env=env)


def spawn_detached_activation(staged_dir: str | Path,
                              install_root: str | Path,
                              *, restart_args: list[str] | None = None,
                              env: dict[str, str] | None = None) -> bool:
    """Parent side: launch the detached worker for --activate-update.

    Returns True when the worker process was launched. The daemon calls
    this, prints one honest line, and exits 0 — the worker owns the rest.
    """
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        try:
            from scripts.launch_detached import detached_creationflags
            kwargs["creationflags"] = detached_creationflags()
        except Exception:
            kwargs["creationflags"] = 0x00000008 | 0x00000200
    code_expr = (
        "import sys; sys.path.insert(0, r'{root}'); "
        "from aali_node.activate import run_activation_cli; "
        "raise SystemExit(run_activation_cli(r'{staged}', r'{install}', "
        "restart_args={restart}))"
    ).format(root=Path(__file__).resolve().parents[1], staged=staged_dir,
             install=install_root, restart=restart_args or [])
    try:
        if getattr(sys, "frozen", False):
            raise OSError(
                "frozen builds cannot run the activation worker with "
                "python -c; activation is a source-deployment path")
        subprocess.Popen(
            [sys.executable, "-c", code_expr],
            cwd=str(install_root),
            env=dict(env) if env else None,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, **kwargs)
        return True
    except OSError:
        return False
