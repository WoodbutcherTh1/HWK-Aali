"""Tests for file_agent/repo_map.py (Aider-parity repository map)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent.repo_map import (  # noqa: E402
    RepoMapError,
    estimate_tokens,
    generate_repo_map,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "class Greeter:\n"
        "    def hello(self):\n"
        "        return 'hi'\n"
        "\n"
        "def standalone():\n"
        "    return Path('.')\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.ts").write_text(
        'import { thing } from "./dep";\n'
        "\n"
        "export class Widget {\n"
        "  run() { return thing; }\n"
        "}\n"
        "\n"
        "export interface Shape { id: string; }\n"
        "export type Mode = 'a' | 'b';\n"
        "export function build() { return 1; }\n"
        "export const quick = async () => 2;\n",
        encoding="utf-8",
    )
    (tmp_path / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "gen.py").write_text("def gen(): pass\n", encoding="utf-8")
    (tmp_path / "src" / "drop.py").write_text("def dropped(): pass\n", encoding="utf-8")
    (tmp_path / "src" / "keep.py").write_text("def kept(): pass\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "ignored.py\n"
        "generated/\n"
        "src/*.py\n"
        "!src/keep.py\n",
        encoding="utf-8",
    )
    return tmp_path


def test_map_lists_python_symbols_and_imports(workspace: Path) -> None:
    text = generate_repo_map(workspace)
    assert "app.py" in text
    assert "class Greeter" in text
    assert "method Greeter.hello" in text
    assert "def standalone" in text
    assert "imports: os, pathlib" in text


def test_map_lists_typescript_symbols_and_imports(workspace: Path) -> None:
    text = generate_repo_map(workspace)
    assert "src/util.ts" in text
    assert "class Widget" in text
    assert "interface Shape" in text
    assert "type Mode" in text
    assert "function build" in text
    assert "function quick" in text
    assert "./dep" in text


def test_gitignore_excludes_and_negation_reincludes(workspace: Path) -> None:
    text = generate_repo_map(workspace)
    assert "ignored.py" not in text
    assert "generated/gen.py" not in text
    assert "src/drop.py" not in text
    assert "src/keep.py" in text


def test_token_budget_is_a_hard_bound(workspace: Path) -> None:
    text = generate_repo_map(workspace, max_tokens=100)
    assert estimate_tokens(text) <= 100


def test_empty_repository_is_honest(tmp_path: Path) -> None:
    text = generate_repo_map(tmp_path)
    assert "source files: 0" in text
    assert "No supported source files" in text


def test_invalid_workspace_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(RepoMapError):
        generate_repo_map(missing)
    with pytest.raises(RepoMapError):
        generate_repo_map(tmp_path, max_tokens=10)


def test_binary_and_large_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "binary.py").write_bytes(b"\x00\x01\x02")
    (tmp_path / "large.py").write_text("#" * 600_000, encoding="utf-8")
    text = generate_repo_map(tmp_path)
    assert "binary.py" not in text
    assert "large.py" not in text


def test_symlink_outside_workspace_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("def secret(): pass\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "escape.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink privileges unavailable on this host")
    text = generate_repo_map(workspace)
    assert "escape.py" not in text
    assert "secret" not in text
