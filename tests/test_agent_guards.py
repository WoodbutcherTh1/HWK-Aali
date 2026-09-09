"""Regression tests for the 2026-09-09 owner-transcript failures.

Live transcript: the 7b brain answered chat with literal "{}", parroted the
user's message back, and switched languages. These pin the agent-loop guards:
shapeless-JSON parsing, echo rejection, and the empty-reply retry with
feedback (the user must never see raw protocol JSON or an empty answer).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

import agent_loop as al  # noqa: E402


# ------------------------------------------------ shapeless JSON ("{}" leak)
def test_parse_bare_json_object_is_empty_text_not_raw() -> None:
    kind, value, args = al._parse_local_model_response("{}")
    assert kind == "text"
    assert value == ""  # empty -> loop retries; user never sees "{}"
    assert args is None


def test_parse_shell_object_keeps_scanning_for_protocol_object() -> None:
    text = '{} {"tool": "final", "content": "hello there"}'
    kind, value, args = al._parse_local_model_response(text)
    assert kind == "final"
    assert value == "hello there"
    assert args is None


def test_parse_json_with_plain_content_returns_content() -> None:
    kind, value, _ = al._parse_local_model_response('{"content": "أهلاً"}')
    assert kind == "text"
    assert value == "أهلاً"


def test_parse_real_protocol_still_works() -> None:
    kind, name, args = al._parse_local_model_response(
        '{"tool": "write_file", "arguments": {"path": "a.txt", "content": "x"}}')
    assert kind == "tool"
    assert name == "write_file"
    assert args["path"] == "a.txt"


# ------------------------------------------------------------- echo guard
def test_echo_helper_detects_verbatim_parrot() -> None:
    assert al._is_echo_of_user("hi Aali how are you ?", "hi Aali how are you ?")


def test_echo_helper_ignores_case_and_punctuation() -> None:
    assert al._is_echo_of_user("HI aali!!! how are you?", "hi Aali how are you")


def test_real_answer_is_not_an_echo() -> None:
    assert not al._is_echo_of_user(
        "I'm doing great, thanks for asking!", "hi how are you")


def test_echo_of_long_message_with_short_addition() -> None:
    msg = "please write me a very long story about dragons and knights"
    assert al._is_echo_of_user(msg + " ok", msg)
    answer = ("Here is a story: Once upon a time a dragon met a knight "
              "and they became friends forever and ever.")
    assert not al._is_echo_of_user(answer, msg)


# --------------------------------------- empty reply -> retry with feedback
def test_empty_reply_retries_then_answers(monkeypatch) -> None:
    def fake_chat(messages, **kwargs):
        if len(messages) == 2:  # first turn (system+user): the shell "{}"
            return {"content": "{}"}
        if len(messages) == 4:  # after feedback: a proper final answer
            assert "فارغاً" in messages[-1]["content"]
            return {"content": json.dumps(
                {"tool": "final", "content": "Hello! I am here."},
                ensure_ascii=False)}
        raise AssertionError(f"unexpected turn count {len(messages)}")

    monkeypatch.setattr(al, "_ollama_chat", fake_chat)
    monkeypatch.setattr(al, "_local_tool_call", lambda m: None)
    out = al._ollama_agent_loop(
        "hello", Path("."), "test", max_iterations=4, history=[])
    assert out == "Hello! I am here."


def test_empty_reply_gives_up_honestly(monkeypatch) -> None:
    monkeypatch.setattr(al, "_ollama_chat", lambda m, **k: {"content": "{}"})
    monkeypatch.setattr(al, "_local_tool_call", lambda m: None)
    out = al._ollama_agent_loop(
        "hello", Path("."), "test", max_iterations=8, history=[])
    assert "تعثّر" in out
    assert "{}" not in out


def test_echo_reply_retries_then_answers(monkeypatch) -> None:
    user = "hi Aali how are you ?"

    def fake_chat(messages, **kwargs):
        if len(messages) == 2:  # first turn: verbatim parrot
            return {"content": user}
        assert len(messages) == 4
        return {"content": json.dumps(
            {"tool": "final", "content": "I'm great, thanks for asking!"},
            ensure_ascii=False)}

    monkeypatch.setattr(al, "_ollama_chat", fake_chat)
    monkeypatch.setattr(al, "_local_tool_call", lambda m: None)
    out = al._ollama_agent_loop(
        user, Path("."), "test", max_iterations=4, history=[])
    assert out == "I'm great, thanks for asking!"
