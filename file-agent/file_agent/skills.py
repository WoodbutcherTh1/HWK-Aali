"""Skills: small markdown playbooks the agent can list and load on demand —
the same idea as Claude's own Skills feature, kept intentionally simple.

A skill is a markdown file under <repo_root>/skills/<name>.md:

    ---
    name: n8n-workflow
    description: When to build an n8n automation and how to hand it to the user.
    ---
    ... instructions ...

Skills live at the repo root (not inside the sandboxed per-conversation
workspace) because they are shared playbooks, not per-session files. The
agent calls list_skills to see what is available (name + one-line
description only — cheap) and use_skill(name) to load the full body only
when it looks relevant, mirroring how Claude decides whether to open a
skill's SKILL.md before using it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _skills_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "skills"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, match.group(2)


def list_skills(workspace_root: str | Path) -> dict[str, Any]:
    directory = _skills_dir()
    if not directory.is_dir():
        return {"skills": []}
    out = []
    for path in sorted(directory.glob("*.md")):
        try:
            meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append({
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
        })
    return {"skills": out}


def use_skill(name: str, workspace_root: str | Path) -> dict[str, Any]:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    path = _skills_dir() / f"{safe_name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"skill not found: {name}")
    _, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return {"name": safe_name, "instructions": body.strip()}
