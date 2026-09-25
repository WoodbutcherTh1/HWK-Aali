"""Part 5.2 + 5.3 (2026-09-25): prompt-leak output filter + canaries.

Contract:
- L1: an 8-word shingle shared with SYSTEM_PROMPT triggers redaction; a
  reply that merely QUOTES a short prompt phrase (canned-answer style,
  e.g. the deterministic identity card) passes untouched.
- L2: ANY canary mark (XMARK-7731 / LEXSEAL-5219 / GLINTQUILL-3407) in
  an output = whole-reply degrade (the prompt itself leaked).
- L3: raw protocol JSON lines are dropped line-wise; clean text survives.
- L4: AALI_* env assignments are dropped; other content survives.
- L5: sk-/pk- secret shapes are dropped.
- Clean replies pass byte-identical. Every redaction writes a
  CONTENT-FREE incident record (kinds + hash, never the leaked text).
- agent_loop() end-to-end: a leaking brain's reply is redacted before
  the user sees it (monkeypatched inner loop).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_loop as al  # noqa: E402


@pytest.fixture()
def incident_log(tmp_path, monkeypatch):
    log = tmp_path / "security_incidents.log"
    monkeypatch.setattr(al, "_LEAK_INCIDENT_LOG", log)
    return log


# ————— L1 shingles —————

def test_l1_whole_prompt_repeat_degrades():
    leak = " ".join(al.SYSTEM_PROMPT.split()[:60])
    out = al._redact_prompt_leak(leak, "hi")
    assert "careful local file assistant" not in out
    assert "rephrasing" in out or "إعادة الصياغة" in out


def test_l1_short_quote_passes():
    """The deterministic identity card quotes a SHORT phrase from the
    prompt — under 8 shared consecutive words — so it must NOT trip L1."""
    ok = ("I'm Aali ✦ — the local assistant on your machine, built and "
          "trained by team HWK entirely in-house.")
    assert al._redact_prompt_leak(ok, "hi") == ok


def test_l1_dominating_leak_degrades_and_l3_lines_survive():
    """L1 is a WHOLE-reply verdict by design (an 8-word shingle spanning
    one line means that line is prompt material; with the shingle check
    being corpus-wide, the safe contract is degrade-not-salvage). L3/L4/L5
    are surgical line drops (pinned by their own tests)."""
    leak_line = " ".join(al.SYSTEM_PROMPT.split()[10:40])
    out = al._redact_prompt_leak(leak_line, "hi")
    assert leak_line not in out
    # L3 stays surgical: clean text around a protocol-JSON line survives.
    mixed = 'Here is your answer:\n{"tool": "final", "content": "x"}\nEnjoy!'
    out3 = al._redact_prompt_leak(mixed, "hi")
    assert "Here is your answer:" in out3 and '"tool"' not in out3


# ————— L2 canaries —————

def test_l2_canary_degrades_whole_reply():
    leak = ("My instructions say XMARK-7731 which means I must obey. "
            "Anything else you want?")
    out = al._redact_prompt_leak(leak, "hi")
    assert "XMARK" not in out


def test_l2_all_three_canaries_are_monitored():
    for c in al._CANARIES:
        assert al._CANARY_RE.search(c)
    assert len(al._CANARIES) == 3


def test_canaries_exist_in_system_prompt():
    """5.3: the marks are really planted in the prompt (the filter is
    useless if the canaries are missing from what the brain receives)."""
    for c in al._CANARIES:
        assert c in al.SYSTEM_PROMPT


# ————— L3/L4/L5 line-level —————

def test_l3_protocol_json_dropped_lines():
    reply = ('Here is what you asked:\n{"tool": "final", "content": "secret plan"}\n'
             "And the summary: all good.")
    out = al._redact_prompt_leak(reply, "hi")
    assert '"tool"' not in out and "all good" in out


def test_l4_env_assign_dropped():
    reply = "Sure!\nAALI_API_KEY=supersecret123\nThat is all."
    out = al._redact_prompt_leak(reply, "hi")
    assert "AALI_API_KEY" not in out and "That is all." in out


def test_l5_secret_shape_dropped():
    reply = "The key is sk-abcdefghij1234567890 keep it safe."
    out = al._redact_prompt_leak(reply, "hi")
    assert "sk-abcdefghij" not in out


def test_all_lines_dropped_degrades_honestly():
    reply = '{"tool": "final", "content": "x"}'
    out = al._redact_prompt_leak(reply, "مرحبا")
    assert "إعادة الصياغة" in out


# ————— clean pass-through + incident log —————

def test_clean_reply_byte_identical():
    ok = "الجواب: تم إنجاز المهمة بنجاح.\nSecond line with details."
    assert al._redact_prompt_leak(ok, "hi") == ok


def test_incident_log_is_content_free(incident_log):
    leak = " ".join(al.SYSTEM_PROMPT.split()[:60])
    al._redact_prompt_leak(leak, "hi", where="test")
    rows = [json.loads(l) for l in
            incident_log.read_text(encoding="utf-8").splitlines() if l]
    assert rows and rows[0]["kinds"] == ["L1_prompt_shingle"]
    assert rows[0]["reply_chars"] == len(leak)
    assert len(rows[0]["reply_sha16"]) == 16
    # the leaked text itself must NEVER be in the log
    assert "careful local file assistant" not in incident_log.read_text(
        encoding="utf-8")


def test_matcher_reports_all_kinds():
    match = al.build_leak_matcher()
    nasty = ('{"tool": "final"} AALI_TOKEN=abc sk-abcdefghij123 '
             "XMARK-7731 " + " ".join(al.SYSTEM_PROMPT.split()[:12]))
    kinds = match(nasty)
    assert {"L1_prompt_shingle", "L2_canary", "L3_protocol_json",
            "L4_env_assign", "L5_secret_shape"} <= set(kinds)


# ————— end-to-end through agent_loop() —————

def test_agent_loop_redacts_before_user(monkeypatch, tmp_path):
    leak = " ".join(al.SYSTEM_PROMPT.split()[:60])
    monkeypatch.setattr(al, "_agent_loop",
                        lambda *a, **k: leak)
    monkeypatch.setattr(al.aali_emoji, "decorate",
                        lambda reply, msg: reply)
    out = al.agent_loop("hello", tmp_path, mode="local", print_final=False)
    assert "careful local file assistant" not in out
    assert "rephrasing" in out or "إعادة الصياغة" in out


def test_probe_classifies_with_real_filter():
    """The 5.1 probe reuses the 5.2 matcher — importable and functional."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import prompt_leak_probe as probe  # noqa: PLC0415
    kinds = probe.classify('{"tool": "final"} and AALI_KEY=x')
    assert "L3_protocol_json" in kinds and "L4_env_assign" in kinds
    assert probe.classify("A perfectly normal answer.") == []
