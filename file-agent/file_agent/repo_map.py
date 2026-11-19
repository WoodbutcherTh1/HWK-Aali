"""Token-budgeted repository map for the Aali coding agent.

Aider's biggest advantage is not a bigger prompt; it is a compact, relevant
picture of the repository.  This module builds that picture without adding a
hard dependency:

* **tree-sitter when available** — accurate classes/functions/imports for
  Python and TypeScript/JavaScript.
* **regex fallback otherwise** — same information, less precision.  The
  fallback is what runs in this repository's default venv today, so it is
  tested as a first-class path rather than a degraded afterthought.

The output is deterministic (sorted paths), safe (never follows a symlink out
of the workspace, never reads a file over 512 KB) and bounded (default 4000
tokens, using a conservative chars/4 estimate for code).

Public API::

    generate_repo_map(workspace_path, max_tokens=4000) -> str
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

DEFAULT_MAX_TOKENS = 4_000
MAX_FILES = 2_000
MAX_FILE_BYTES = 512_000
MAX_SYMBOLS_PER_FILE = 40
MAX_IMPORTS_PER_FILE = 12

#: Directories that are never part of the code the agent should reason about.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".aali", ".venv", "venv", "env",
    "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".next", ".nuxt", "dist", "build", "target",
    "bin", "obj", "coverage", ".idea", ".vscode", ".tox",
}

SOURCE_SUFFIXES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
}


class RepoMapError(Exception):
    """Expected, user-facing repository-map error."""


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------

def _glob_to_regex(pattern: str) -> str:
    """Translate a gitignore glob to a path-aware regex body."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return "".join(out)


class _GitIgnore:
    """A small, ordered gitignore matcher (last matching rule wins)."""

    def __init__(self, rules: Iterable[str] = ()) -> None:
        self._rules: list[tuple[bool, re.Pattern[str]]] = []
        for raw in rules:
            self._add(raw)

    def _add(self, raw: str) -> None:
        line = str(raw).strip()
        if not line or line.startswith("#"):
            return
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        if not line:
            return
        line = line.replace("\\", "/")
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = line.startswith("/")
        line = line.lstrip("/")
        if not line:
            return
        body = _glob_to_regex(line)
        if anchored or "/" in line:
            expression = "^" + body + r"(?:/|$)"
        else:
            expression = r"(?:^|/)" + body + r"(?:/|$)"
        try:
            compiled = re.compile(expression, re.IGNORECASE)
        except re.error:
            return
        self._rules.append((negated, compiled))

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def ignored(self, relative_path: str, *, is_dir: bool = False) -> bool:
        path = str(relative_path).replace("\\", "/").lstrip("./")
        ignored = False
        for negated, pattern in self._rules:
            if pattern.search(path):
                ignored = not negated
        return ignored


def _load_gitignore(root: Path) -> _GitIgnore:
    rules: list[str] = []
    for candidate in (root / ".gitignore", root / ".git" / "info" / "exclude"):
        try:
            rules.extend(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return _GitIgnore(rules)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

_PY_CLASS = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>[A-Za-z_]\w*)")
_PY_DEF = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(")
_PY_IMPORT = re.compile(
    r"^(?:from\s+(?P<from>[A-Za-z_][\w.]*)\s+import|import\s+(?P<plain>[A-Za-z_][\w.]*))"
)

_TS_CLASS = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_TS_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_TS_INTERFACE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"
)
_TS_TYPE = re.compile(
    r"^\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*="
)
_TS_CONST_FN = re.compile(
    r"^\s*(?:export\s+)?const\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
_TS_IMPORT = re.compile(
    r"^\s*import\s+(?:type\s+)?(?:[^'\"]*?\s+from\s+)?['\"](?P<module>[^'\"]+)['\"]"
)
_TS_EXPORT_FROM = re.compile(
    r"^\s*export\s+(?:type\s+)?(?:\*|\{[^}]*\})\s+from\s+['\"](?P<module>[^'\"]+)['\"]"
)


def _parse_python_regex(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    symbols: list[tuple[str, str]] = []
    imports: list[str] = []
    classes: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while classes and indent <= classes[-1][0]:
            classes.pop()
        match = _PY_CLASS.match(line)
        if match:
            name = match.group("name")
            classes.append((indent, name))
            symbols.append(("class", name))
            continue
        match = _PY_DEF.match(line)
        if match:
            name = match.group("name")
            if classes:
                symbols.append(("method", f"{classes[-1][1]}.{name}"))
            else:
                symbols.append(("def", name))
            continue
        match = _PY_IMPORT.match(line)
        if match:
            module = match.group("from") or match.group("plain")
            if module and module not in imports:
                imports.append(module)
    return symbols, imports


def _parse_typescript_regex(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    symbols: list[tuple[str, str]] = []
    imports: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        for kind, pattern in (
            ("class", _TS_CLASS),
            ("function", _TS_FUNCTION),
            ("interface", _TS_INTERFACE),
            ("type", _TS_TYPE),
            ("function", _TS_CONST_FN),
        ):
            match = pattern.match(line)
            if match:
                symbols.append((kind, match.group("name")))
                break
        match = _TS_IMPORT.match(line) or _TS_EXPORT_FROM.match(line)
        if match:
            module = match.group("module")
            if module and module not in imports:
                imports.append(module)
    return symbols, imports


def _tree_sitter_language(language: str) -> Any | None:
    """Return a tree-sitter Language, or None when the optional deps are absent."""
    try:
        import tree_sitter as ts  # type: ignore
        if language == "python":
            import tree_sitter_python as grammar  # type: ignore

            capsule = grammar.language()
        else:
            import tree_sitter_typescript as grammar  # type: ignore

            capsule = (getattr(grammar, "language_typescript", None)
                       or getattr(grammar, "language_tsx", None))
            capsule = capsule() if callable(capsule) else capsule
        return ts.Language(capsule)
    except Exception:  # noqa: BLE001 - optional path must never break the map
        return None


def _parse_tree_sitter(text: str, language: str) -> tuple[list[tuple[str, str]], list[str]] | None:
    tree_language = _tree_sitter_language(language)
    if tree_language is None:
        return None
    try:
        import tree_sitter as ts  # type: ignore

        try:
            parser = ts.Parser(tree_language)
        except TypeError:  # older API
            parser = ts.Parser()
            parser.language = tree_language  # type: ignore[attr-defined]
        tree = parser.parse(text.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return None

    symbols: list[tuple[str, str]] = []
    imports: list[str] = []

    def walk(node: Any) -> None:
        node_type = getattr(node, "type", "")
        if node_type in {
            "class_definition", "function_definition",
            "class_declaration", "function_declaration",
            "interface_declaration", "type_alias_declaration",
        }:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text.encode("utf-8")[name_node.start_byte:name_node.end_byte].decode(
                    "utf-8", "replace")
                kind = {
                    "class_definition": "class",
                    "function_definition": "def",
                    "class_declaration": "class",
                    "function_declaration": "function",
                    "interface_declaration": "interface",
                    "type_alias_declaration": "type",
                }.get(node_type, "symbol")
                if (node_type == "function_definition"
                        and getattr(getattr(node, "parent", None), "type", "") == "class_definition"):
                    kind = "method"
                symbols.append((kind, name))
        elif node_type in {"import_statement", "import_from_statement"}:
            raw = text.encode("utf-8")[node.start_byte:node.end_byte].decode(
                "utf-8", "replace")
            match = re.search(r"(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", raw)
            if match:
                module = match.group(1) or match.group(2)
                if module and module not in imports:
                    imports.append(module)
        elif node_type == "import_statement":
            pass
        for child in getattr(node, "children", []) or []:
            walk(child)

    try:
        walk(tree.root_node)
    except Exception:  # noqa: BLE001
        return None
    if not symbols and not imports:
        return None
    return symbols, imports


def _parse_file(path: Path, language: str) -> tuple[list[tuple[str, str]], list[str]]:
    try:
        raw = path.read_bytes()
    except OSError:
        return [], []
    if b"\x00" in raw[:2048]:
        return [], []
    text = raw.decode("utf-8", "replace")
    parsed = _parse_tree_sitter(text, language)
    if parsed is not None:
        return parsed
    if language == "python":
        return _parse_python_regex(text)
    return _parse_typescript_regex(text)


# ---------------------------------------------------------------------------
# walking
# ---------------------------------------------------------------------------

def _iter_sources(root: Path, ignore: _GitIgnore) -> Iterator[tuple[str, Path, str]]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        kept: list[str] = []
        for name in sorted(dirnames):
            if name in SKIP_DIRS or name.startswith("."):
                continue
            relative = f"{rel_dir}/{name}" if rel_dir != "." else name
            if ignore.ignored(relative, is_dir=True):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            suffix = Path(name).suffix.lower()
            language = SOURCE_SUFFIXES.get(suffix)
            if language is None or name.startswith("."):
                continue
            relative = f"{rel_dir}/{name}" if rel_dir != "." else name
            if ignore.ignored(relative):
                continue
            candidate = current / name
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
                if resolved.stat().st_size > MAX_FILE_BYTES:
                    continue
                with candidate.open("rb") as handle:
                    if b"\x00" in handle.read(2048):
                        continue  # binary content, not source
            except (OSError, ValueError):
                continue
            yield relative, candidate, language
            count += 1
            if count >= MAX_FILES:
                return


def _section(relative: str, symbols: list[tuple[str, str]],
             imports: list[str]) -> str:
    lines = [relative]
    for kind, name in symbols[:MAX_SYMBOLS_PER_FILE]:
        lines.append(f"  {kind} {name}")
    if len(symbols) > MAX_SYMBOLS_PER_FILE:
        lines.append(f"  … {len(symbols) - MAX_SYMBOLS_PER_FILE} more symbols")
    if imports:
        shown = ", ".join(imports[:MAX_IMPORTS_PER_FILE])
        suffix = " …" if len(imports) > MAX_IMPORTS_PER_FILE else ""
        lines.append(f"  imports: {shown}{suffix}")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Conservative code-token estimate (ASCII code runs ~4 chars/token)."""
    return max(1, math.ceil(len(text) / 4))


def generate_repo_map(workspace_path: str | Path,
                      max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Return a deterministic, token-budgeted summary of the codebase.

    Raises RepoMapError when the path is not a readable directory.
    """
    try:
        budget = int(max_tokens)
    except (TypeError, ValueError) as exc:
        raise RepoMapError("max_tokens must be an integer") from exc
    if budget < 100:
        raise RepoMapError("max_tokens must be at least 100")
    budget = min(budget, 100_000)
    char_budget = budget * 4

    root = Path(workspace_path).expanduser().resolve()
    if not root.exists():
        raise RepoMapError(f"workspace does not exist: {root}")
    if not root.is_dir():
        raise RepoMapError(f"workspace is not a directory: {root}")

    ignore = _load_gitignore(root)
    sections: list[str] = []
    total_files = 0
    for relative, path, language in _iter_sources(root, ignore):
        total_files += 1
        symbols, imports = _parse_file(path, language)
        sections.append(_section(relative, symbols, imports))

    header = (
        "Aali repository map\n"
        f"root: {root.name}\n"
        f"source files: {total_files}\n"
        f".gitignore rules: {ignore.rule_count}\n"
    )
    if not sections:
        return header + "\nNo supported source files (.py/.ts/.tsx/.js/.jsx).\n"

    output = header
    omitted = 0
    for index, section in enumerate(sections):
        reserve = 80
        if len(output) + len(section) + reserve > char_budget:
            omitted = len(sections) - index
            break
        output += "\n" + section + "\n"
    if omitted:
        output += f"\n… {omitted} more file(s) omitted (token budget).\n"

    # Hard guarantee: never return more than the requested budget (approx).
    if len(output) > char_budget:
        output = output[: max(0, char_budget - 24)].rstrip() + "\n… truncated.\n"
    return output


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "RepoMapError",
    "estimate_tokens",
    "generate_repo_map",
]
