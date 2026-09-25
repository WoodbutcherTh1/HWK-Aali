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


# ------------------------------- boilerplate disclaimer guard (2026-09-24)
# Owner transcript: "tell me more about what you built today" → "As an AI
# language model, I don't have the ability to build things…" — a shame for a
# machine that builds things all day. The guard retries with feedback; a
# second strike passes through (never loops forever).

def test_boilerplate_detector_catches_the_shame() -> None:
    assert al._is_boilerplate_disclaimer(
        "As an AI language model, I don't have the ability to build things "
        "in the traditional sense, but I can provide information.")
    assert al._is_boilerplate_disclaimer(
        "I'm sorry, my current capabilities are limited to assisting with "
        "tasks within the confines of the workspace.")
    assert al._is_boilerplate_disclaimer(
        "أنا مجرد نموذج ذكاء اصطناعي ولا أستطيع تنفيذ الأوامر على جهازك.")


def test_boilerplate_detector_spares_real_answers() -> None:
    # Real statements about the world — no self-reference — must NOT trip it.
    assert not al._is_boilerplate_disclaimer(
        "You cannot divide by zero — it is undefined in mathematics.")
    assert not al._is_boilerplate_disclaimer(
        "Dogs have been domesticated for thousands of years and developed "
        "into a wide variety of breeds.")
    # A genuine, useful refusal states what it WILL do instead.
    assert not al._is_boilerplate_disclaimer(
        "I can't delete files outside the workspace, but I can move them "
        "into the workspace folder for you.")


def test_ollama_loop_retries_boilerplate_then_answers(monkeypatch) -> None:
    bad = "As an AI language model, I don't have the ability to build things."

    def fake_chat(messages, **kwargs):
        if len(messages) == 2:  # first turn: the shame
            return {"content": bad}
        assert len(messages) == 4
        feedback = messages[-1]["content"]
        assert "آلي" in feedback and "أي تنصّل" in feedback
        return {"content": json.dumps(
            {"tool": "final", "content":
             "Today I wired the remote-access tunnel and the key-mode server."},
            ensure_ascii=False)}

    monkeypatch.setattr(al, "_ollama_chat", fake_chat)
    monkeypatch.setattr(al, "_local_tool_call", lambda m: None)
    out = al._ollama_agent_loop(
        "tell me more about what you built today", Path("."), "test",
        max_iterations=4, history=[])
    assert "tunnel" in out


def test_ollama_loop_boilerplate_never_reaches_user(monkeypatch) -> None:
    bad = "My current capabilities are limited to basic file management."
    monkeypatch.setattr(al, "_ollama_chat", lambda m, **k: {"content": bad})
    monkeypatch.setattr(al, "_local_tool_call", lambda m: None)
    out = al._ollama_agent_loop(
        "what did you build", Path("."), "test", max_iterations=8, history=[])
    # Two feedback retries, then an honest apology — the shame never passes.
    assert "I stumbled putting together a proper answer" in out
    assert "limited to basic file management" not in out


def test_ollama_loop_boilerplate_arabic_gets_arabic_apology(monkeypatch) -> None:
    bad = "أنا مجرد نموذج ذكاء اصطناعي ولا أستطيع بناء التطبيقات."
    monkeypatch.setattr(al, "_ollama_chat", lambda m, **k: {"content": bad})
    monkeypatch.setattr(al, "_local_tool_call", lambda m: None)
    out = al._ollama_agent_loop(
        "ماذا بنيت اليوم؟", Path("."), "test", max_iterations=8, history=[])
    assert "تعثّرت" in out
    assert "مجرد نموذج" not in out


def test_openai_compat_loop_retries_boilerplate_then_answers(
        monkeypatch, tmp_path) -> None:
    bad = ("I'm sorry, my current capabilities are limited to assisting "
           "with basic file management tasks.")
    good = "Today I shipped the remote-access tunnel and the key-mode server."
    calls = {"n": 0}

    class FakeResp:
        status_code = 200

        def json(self):
            calls["n"] += 1
            content = bad if calls["n"] == 1 else good
            return {"choices": [{"message":
                    {"role": "assistant", "content": content}}]}

        def raise_for_status(self):
            pass

    class FakeClient:
        @staticmethod
        def post(*a, **k):
            return FakeResp()

    monkeypatch.setattr(al, "get_tool_definitions", lambda: [])
    out = al._openai_compat_loop(
        user_message="tell me more about what you built today",
        root=tmp_path,
        request_id="t",
        base_url="http://x/v1/chat/completions",
        api_key="k",
        model="m",
        max_iterations=4,
        history=[],
        policy="auto",
        confirmed=False,
        gate_state=None,
        provider_label="test",
        client=FakeClient)
    assert calls["n"] == 2
    assert out == good


def test_openai_compat_loop_boilerplate_never_reaches_user(
        monkeypatch, tmp_path) -> None:
    bad = "As an AI language model, I don't have the ability to do that."
    calls = {"n": 0}

    class FakeResp:
        status_code = 200

        def json(self):
            calls["n"] += 1
            return {"choices": [{"message":
                    {"role": "assistant", "content": bad}}]}

        def raise_for_status(self):
            pass

    class FakeClient:
        @staticmethod
        def post(*a, **k):
            return FakeResp()

    monkeypatch.setattr(al, "get_tool_definitions", lambda: [])
    out = al._openai_compat_loop(
        user_message="what did you build today",
        root=tmp_path,
        request_id="t",
        base_url="http://x/v1/chat/completions",
        api_key="k",
        model="m",
        max_iterations=8,
        history=[],
        policy="auto",
        confirmed=False,
        gate_state=None,
        provider_label="test",
        client=FakeClient)
    # 2 retries consumed, 3rd strike degrades to the honest apology.
    assert calls["n"] == 3
    assert "I stumbled putting together a proper answer" in out


# ------------------------------------------------- identity truth (2026-09-25)
# Owner transcript: "who are you ? whats your name ? and who bulid u ?" →
# the soup /v1 brain answered "My identity was trained by Alibaba Cloud".
# The base model's baked-in identity leaked through the one loop that had
# no deterministic identity fast-path. These pin the three-layer fix.

def test_identity_fast_name_en() -> None:
    assert "Aali" in (al._identity_fast("what's your name?") or "")


def test_identity_fast_name_ar() -> None:
    assert "آلي" in (al._identity_fast("ما اسمك؟") or "")


def test_identity_fast_builder_en() -> None:
    out = al._identity_fast("who are you ? whats your name ? and who bulid u ?")
    assert out is not None and "HWK" in out and "Alibaba" not in out


def test_identity_fast_builder_ar() -> None:
    out = al._identity_fast("من بنى البرنامج الذي تتحدث من خلاله؟")
    assert out is not None and "فريق HWK" in out


def test_identity_fast_ignores_normal_questions() -> None:
    assert al._identity_fast("what did you build today?") is None
    assert al._identity_fast("who created the light bulb?") is None


def test_scrub_replaces_owner_leak_when_identity_asked() -> None:
    leak = ("I am Aali (آلي), a local assistant running on your device. "
            "My identity was trained by Alibaba Cloud and my creators include "
            "several leading tech companies.")
    out = al._scrub_identity_leak(leak, "who are you ? and who bulid u ?")
    assert "Alibaba" not in out and "HWK" in out


def test_scrub_kills_first_person_maker_claim_without_identity_ask() -> None:
    leak = "I was trained by OpenAI on a large corpus."
    assert "OpenAI" not in al._scrub_identity_leak(leak, "tell me something")


def test_scrub_preserves_legit_third_party_talk() -> None:
    ok = "OpenAI was created by Sam Altman. Anyway, your file is ready."
    assert al._scrub_identity_leak(ok, "hello") == ok


def test_scrub_arabic_maker_claim() -> None:
    leak = "تم تدريبي بواسطة علي بابا سحابة."
    out = al._scrub_identity_leak(leak, "مرحبا")
    assert "علي بابا" not in out and "HWK" in out


def test_scrub_noop_without_brands() -> None:
    ok = "I am Aali, your local assistant."
    assert al._scrub_identity_leak(ok, "hi") == ok


def test_openai_compat_loop_identity_fast_path(monkeypatch, tmp_path) -> None:
    """The soup /v1 brain path must return the deterministic identity card
    WITHOUT contacting the server (the 2026-09-25 leak came through here)."""
    def boom(*a, **k):  # any HTTP attempt = the leak path again
        raise AssertionError("identity fast-path bypassed the brain")

    monkeypatch.setattr(al, "requests", boom)
    monkeypatch.setattr(al, "get_tool_definitions", boom)
    out = al._openai_compat_loop(
        user_message="who built you?",
        root=tmp_path,
        request_id="t",
        base_url="http://x/v1/chat/completions",
        api_key="k",
        model="m",
        max_iterations=8,
        history=[],
        policy="auto",
        confirmed=False,
        gate_state=None,
        provider_label="test",
    )
    assert "HWK" in out and "Alibaba" not in out
    assert "As an AI language model" not in out
