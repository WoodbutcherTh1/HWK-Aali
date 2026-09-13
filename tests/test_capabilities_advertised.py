"""Tests for Aali's advertised capabilities: running commands on the user's
behalf and building apps & games end-to-end.

Covers (2026-09-13): SYSTEM_PROMPT advertises run_command + app/game building
(EN + AR), the deterministic capability answers list them, the dedicated
"can you run commands / build games?" triggers fire in both languages, and the
build-apps skill is listed and loadable through the real skills registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

import agent_loop as al  # noqa: E402


# ------------------------------------------------------- system prompt (EN)
def test_system_prompt_advertises_run_command() -> None:
    assert "run_command" in al.SYSTEM_PROMPT
    assert "BUILD apps and games" in al.SYSTEM_PROMPT


def test_system_prompt_names_allowlisted_runtimes() -> None:
    for tool in ("python", "pip", "node", "npm", "git"):
        assert tool in al.SYSTEM_PROMPT


# ------------------------------------------------------- system prompt (AR)
def test_arabic_prompt_advertises_commands_and_building() -> None:
    assert "تنفّذ أوامر حقيقية نيابة عن المستخدم عبر run_command" in al.SYSTEM_PROMPT
    assert "تبني للمستخدم تطبيقات وألعابًا" in al.SYSTEM_PROMPT


# --------------------------------------- deterministic capability list answers
def test_english_capability_answer_lists_building() -> None:
    kind, value = al._local_tool_call("what can you do?")
    assert kind == "final"
    assert "App & game building" in value["content"]
    assert "Safe commands" in value["content"]


def test_arabic_capability_answer_lists_building() -> None:
    kind, value = al._local_tool_call("ماذا تستطيع أن تفعل؟")
    assert kind == "final"
    assert "بناء تطبيقات وألعاب" in value["content"]
    assert "أوامر آمنة" in value["content"]


# ---------------------------------- "can you run commands / build games?" Qs
def test_english_can_you_run_commands_gets_deterministic_yes() -> None:
    kind, value = al._local_tool_call("can you run commands for me?")
    assert kind == "final"
    content = value["content"]
    assert content.startswith("Yes")
    assert "run_command" not in content  # user-facing text, not tool names
    assert "python" in content and "npm" in content


def test_english_can_you_build_games_gets_deterministic_yes() -> None:
    kind, value = al._local_tool_call("can you build games?")
    assert kind == "final"
    assert "build complete apps and games" in value["content"]


def test_arabic_can_you_run_commands_gets_deterministic_yes() -> None:
    kind, value = al._local_tool_call("هل تستطيع تنفيذ أوامر نيابة عني؟")
    assert kind == "final"
    content = value["content"]
    assert content.startswith("نعم")
    assert "أوامر حقيقية" in content


def test_arabic_make_me_a_game_gets_deterministic_yes() -> None:
    kind, value = al._local_tool_call("اصنع لي لعبة ثعبان")
    assert kind == "final"
    assert "ألعاب وتطبيقات" in value["content"]


# ------------------------------------------------- build-apps skill registry
def test_build_apps_skill_listed_with_description() -> None:
    from file_agent.skills import list_skills

    skills = {s["name"]: s["description"] for s in list_skills(".")["skills"]}
    assert "build-apps" in skills
    assert "run_command" in skills["build-apps"]


def test_build_apps_skill_loads_real_instructions() -> None:
    from file_agent.skills import use_skill

    loaded = use_skill("build-apps", ".")
    assert loaded["name"] == "build-apps"
    body = loaded["instructions"]
    for needle in ("run_command", "pip install", "Report honestly", "machine-ops"):
        assert needle in body
