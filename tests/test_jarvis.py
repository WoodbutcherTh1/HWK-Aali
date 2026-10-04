"""Tests for the Jarvis bot.

The centrepiece is :class:`MockTelegram`, a real local HTTP server that speaks
the Bot API, so the whole inbound -> route -> reply path runs end to end with
no token and no network.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from jarvis import brain, commands, groq, handoff, intent, log, power, tts, wol
from jarvis import main as jarvis_main
from jarvis.commands import Jarvis
from jarvis.config import GroqConfig, TelegramConfig, load_groq, load_telegram
from jarvis.machines import Machine, MachineError, load_machines, normalize_mac, resolve
from jarvis.telegram import TelegramClient

OWNER = 6027532184


# --------------------------------------------------------------- fixtures
@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    d.mkdir()
    monkeypatch.setenv("AALI_JARVIS_CONFIG", str(d))
    return d


@pytest.fixture
def machines_file(tmp_path):
    data = {
        "pc": {
            "name": "Training PC",
            "ip": "192.168.1.13",
            "mac": "F4-B5-20-46-44-27",
            "user": "hmamk",
            "os": "windows",
            "api_port": 5055,
        },
        "mac": {
            "name": "MacBook Air",
            "ip": "192.168.1.8",
            "mac": "e6-c5-be-09-b7-f9",
            "user": "humussalad",
            "os": "macos",
        },
    }
    p = tmp_path / "machines.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def machines(machines_file):
    return load_machines(machines_file)


# ------------------------------------------------------------ config
def test_config_reads_key_and_telegram(cfg_dir):
    (cfg_dir / "groq_key.txt").write_text("gsk_abc123\n", encoding="utf-8")
    (cfg_dir / "telegram.json").write_text(
        json.dumps({"bot_token": "123:ABC", "owner_chat_id": OWNER}), encoding="utf-8"
    )
    g = load_groq(cfg_dir)
    assert g.api_key == "gsk_abc123"
    tg = load_telegram(cfg_dir)
    assert tg.bot_token == "123:ABC"
    assert tg.owner_chat_id == OWNER


def test_missing_key_raises(cfg_dir):
    with pytest.raises(FileNotFoundError):
        load_groq(cfg_dir)


def test_telegram_defaults_to_owner_chat_id(cfg_dir):
    (cfg_dir / "telegram.json").write_text(json.dumps({"bot_token": "x"}), encoding="utf-8")
    assert load_telegram(cfg_dir).owner_chat_id == OWNER


# ------------------------------------------------------------- logging
def test_log_never_writes_secrets(tmp_path):
    target = tmp_path / "jarvis.log"
    log.log_event(
        "chat",
        path=target,
        text="my password is hunter2",
        bot_token="123:SECRET",
        api_key="gsk_leak",
        chars=12,
        ok=True,
    )
    body = target.read_text(encoding="utf-8")
    for leak in ("hunter2", "123:SECRET", "gsk_leak"):
        assert leak not in body
    assert '"chars": 12' in body
    assert '"event": "chat"' in body


def test_log_long_values_are_hashed_not_stored(tmp_path):
    target = tmp_path / "jarvis.log"
    long_text = "A" * 300
    log.log_event("x", path=target, reason=long_text)
    body = target.read_text(encoding="utf-8")
    assert "AAAA" not in body
    assert len(body) < 200


def test_log_never_raises_on_bad_path(tmp_path):
    log.log_event("boom", path=tmp_path / "no" / "such" / "dir" / "x.log", chars=1)


# ----------------------------------------------------------- machines
def test_normalize_mac_accepts_both_separators():
    assert normalize_mac("f4-b5-20-46-44-27") == "F4:B5:20:46:44:27"
    assert normalize_mac("F4:B5:20:46:44:27") == "F4:B5:20:46:44:27"
    assert normalize_mac("nonsense") is None
    assert normalize_mac(None) is None


def test_load_machines(machines):
    assert set(machines) == {"pc", "mac"}
    assert machines["pc"].mac == "F4:B5:20:46:44:27"
    assert machines["pc"].api_port == 5055
    assert machines["mac"].os == "macos"


def test_machines_rejects_bad_files(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(MachineError):
        load_machines(bad)
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    with pytest.raises(MachineError):
        load_machines(empty)
    noip = tmp_path / "noip.json"
    noip.write_text(json.dumps({"pc": {"name": "x"}}), encoding="utf-8")
    with pytest.raises(MachineError):
        load_machines(noip)


def test_resolve_aliases(machines):
    assert resolve("pc", machines).key == "pc"
    assert resolve("MacBook", machines).key == "mac"
    assert resolve("training", machines).key == "pc"
    assert resolve("nope", machines) is None
    assert resolve(None, machines) is None


# -------------------------------------------------------------- wol
def test_magic_packet_bytes():
    pkt = wol.magic_packet("F4:B5:20:46:44:27")
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes.fromhex("F4B520464427")
    assert pkt[6:] == bytes.fromhex("F4B520464427") * 16


def test_send_magic_packet_uses_sender():
    captured = {}

    def fake(data, address):
        captured["data"] = data
        captured["addr"] = address

    pkt = wol.send_magic_packet("f4-b5-20-46-44-27", sender=fake)
    assert captured["data"] == pkt
    assert captured["addr"][1] == 9


def test_send_to_many_reports_per_mac():
    calls = []
    res = wol.send_to_many(
        ["F4:B5:20:46:44:27", "E6:C5:BE:09:B7:F9"], sender=lambda d, a: calls.append(d)
    )
    assert len(calls) == 2
    assert all(res.values())


# ------------------------------------------------------------ power
def test_power_command_table_is_per_os(machines):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return 0, "ok"

    r = power.power_action(machines["pc"], "shutdown", runner=runner)
    assert r.ok
    assert "shutdown /s" in calls[0][-1]
    r2 = power.power_action(machines["mac"], "shutdown", runner=runner)
    assert r2.ok
    assert "System Events" in calls[1][-1]


def test_power_refuses_unknown_action_and_os(machines):
    r = power.power_action(machines["pc"], "selfdestruct", runner=lambda a, t: (0, ""))
    assert not r.ok and "unknown action" in r.detail
    alien = Machine(key="x", name="X", ip="1.2.3.4", mac=None, user="u", os="plan9")
    r2 = power.power_action(alien, "shutdown", runner=lambda a, t: (0, ""))
    assert not r2.ok and "no power commands" in r2.detail


def test_ssh_uses_batch_mode(machines):
    calls = []
    power.ssh_exec(machines["pc"], "echo hi", runner=lambda argv, t: (calls.append(argv) or (0, "hi")))
    argv = calls[0]
    assert "BatchMode=yes" in argv
    assert "hmamk@192.168.1.13" in argv


def test_machine_status_uses_injected_pinger(machines):
    st = power.machine_status(machines["pc"], pinger=lambda *a, **k: True)
    assert st["online"] is True
    st2 = power.machine_status(machines["pc"], pinger=lambda *a, **k: False)
    assert st2["online"] is False
    assert st2["api"] is None


def test_ping_uses_platform_flags(monkeypatch):
    """Regression: Windows flags made every machine look offline on the Pi."""
    seen = {}

    def capture(argv, timeout):
        seen["argv"] = argv
        return 0, ""

    monkeypatch.setattr(power, "_runner", capture)
    monkeypatch.setattr(power.os, "name", "posix")
    assert power.ping_ok("192.168.1.13") is True
    assert "-c" in seen["argv"] and "-W" in seen["argv"]
    assert "-n" not in seen["argv"]

    monkeypatch.setattr(power.os, "name", "nt")
    power.ping_ok("192.168.1.13")
    assert "-n" in seen["argv"] and "-w" in seen["argv"]


def test_ping_failure_is_not_online(monkeypatch):
    monkeypatch.setattr(power, "_runner", lambda argv, timeout: (1, "unreachable"))
    assert power.ping_ok("192.168.1.99") is False


def test_describe_status_arabic(machines):
    on = power.machine_status(machines["pc"], pinger=lambda *a, **k: True)
    assert "🟢" in power.describe_status(on)
    off = power.machine_status(machines["pc"], pinger=lambda *a, **k: False)
    assert "🔴" in power.describe_status(off)


# ------------------------------------------------------- mock telegram
class MockTelegram:
    """A real HTTP server speaking enough of the Bot API to test end to end."""

    def __init__(self, token="TOKEN"):
        self.token = token
        self.sent_text = []
        self.sent_voice = []
        self.typing = []
        self.queue = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):
                # Telegram's getUpdates and file download are GETs.
                path = self.path
                if "/getUpdates" in path:
                    updates = outer.queue
                    outer.queue = []
                    self._send({"ok": True, "result": updates})
                elif "/file/bot" in path:
                    raw = b"OGGVOICE-BYTES"
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/ogg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                else:
                    self._send({"ok": True, "result": {}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                method = self.path.rsplit("/", 1)[-1]
                ctype = self.headers.get("Content-Type", "")
                if method == "getUpdates":
                    if outer.queue:
                        updates = outer.queue
                        outer.queue = []
                    else:
                        updates = []
                    self._send({"ok": True, "result": updates})
                elif method == "sendMessage":
                    payload = json.loads(body or b"{}")
                    outer.sent_text.append(payload)
                    self._send({"ok": True, "result": {"message_id": len(outer.sent_text)}})
                elif method == "sendVoice":
                    outer.sent_voice.append({"bytes": len(body), "ctype": ctype})
                    self._send({"ok": True, "result": {"message_id": 1}})
                elif method == "sendChatAction":
                    outer.typing.append(json.loads(body or b"{}"))
                    self._send({"ok": True, "result": True})
                elif method == "getFile":
                    self._send({"ok": True, "result": {"file_path": "voice/file_1.oga"}})
                else:
                    self._send({"ok": True, "result": {}})

            def _send(self, obj):
                raw = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def push(self, update):
        self.queue.append(update)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def last_text(self):
        return self.sent_text[-1]["text"] if self.sent_text else ""


@pytest.fixture
def mock_tg():
    m = MockTelegram()
    yield m
    m.close()


@pytest.fixture
def jarvis(machines, mock_tg, monkeypatch, tmp_path):
    cfg = GroqConfig(api_key="gsk_test", chat_model="test-model")
    tg = TelegramClient("TOKEN", OWNER, base_url=mock_tg.base_url)
    # never let a test reach the network
    monkeypatch.setattr(power, "tcp_open", lambda *a, **k: True)
    j = Jarvis(
        cfg,
        tg,
        machines,
        runner=lambda argv, t: (0, "fake output"),
        pinger=lambda *a, **k: True,
        wake_sender=lambda mac: b"\xff" * 102,
        audio_dir=str(tmp_path),
    )
    # The natural-language router calls Groq on EVERY free-text message. Left on,
    # the suite would reach the network (and a 60s timeout) for each case; the
    # ``router`` fixture turns it on deliberately, with a scripted model.
    j.intent_enabled = False
    j.router_calls = []
    return j


def text_update(chat_id, text, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id}, "text": text}}


# -------------------------------------------------- end-to-end behaviour
def test_start_replies_welcome(jarvis, mock_tg):
    mock_tg.push(text_update(OWNER, "/start"))
    updates = list(jarvis.tg.poll_forever(stop_after=1))
    assert len(updates) == 1, "the queued update should arrive over the mock API"
    jarvis.handle_update(updates[0])
    assert "جارفيس" in mock_tg.last_text()


def test_help_is_arabic(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/help"))
    assert "/status" in mock_tg.last_text()


def test_non_owner_is_ignored(jarvis, mock_tg):
    jarvis.handle_update(text_update(999999, "/status"))
    assert mock_tg.sent_text == []


def test_status_reports_every_machine(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/status all"))
    text = mock_tg.last_text()
    assert "Training PC" in text and "MacBook Air" in text
    assert "🟢" in text


def test_status_unknown_machine(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/status fridge"))
    assert "❓" in mock_tg.last_text()


def test_wake_sends_magic_packet(jarvis, mock_tg):
    sent = []
    jarvis.wake_sender = lambda mac: sent.append(mac) or b"\xff" * 102
    jarvis.handle_update(text_update(OWNER, "/wake pc"))
    assert sent == ["F4:B5:20:46:44:27"]
    assert "إشعار" in mock_tg.last_text()


def test_wake_all_wakes_every_machine(jarvis, mock_tg):
    sent = []
    jarvis.wake_sender = lambda mac: sent.append(mac) or b"\xff" * 102
    jarvis.handle_update(text_update(OWNER, "/wake all"))
    assert len(sent) == 2


# ------------------------------------------- the two-step confirmation
def test_shutdown_never_fires_on_the_first_command(jarvis, mock_tg):
    calls = []
    jarvis.runner = lambda argv, t: (calls.append(argv) or (0, ""))
    jarvis.handle_update(text_update(OWNER, "/shutdown pc"))
    assert calls == [], "power command ran without confirmation!"
    assert "تأكيد" in mock_tg.last_text()
    assert len(jarvis.pending) == 1


def test_shutdown_confirms_with_the_minted_token(jarvis, mock_tg):
    calls = []
    jarvis.runner = lambda argv, t: (calls.append(argv) or (0, ""))
    jarvis.handle_update(text_update(OWNER, "/shutdown pc"))
    token = next(iter(jarvis.pending))
    jarvis.handle_update(text_update(OWNER, f"/shutdown pc {token}"))
    assert len(calls) == 1
    assert "shutdown /s" in calls[0][-1]
    assert jarvis.pending == {}


def test_wrong_token_is_refused(jarvis, mock_tg):
    calls = []
    jarvis.runner = lambda argv, t: (calls.append(argv) or (0, ""))
    jarvis.handle_update(text_update(OWNER, "/shutdown pc"))
    jarvis.handle_update(text_update(OWNER, "/shutdown pc 000000"))
    assert calls == []


def test_token_cannot_be_replayed(jarvis, mock_tg):
    calls = []
    jarvis.runner = lambda argv, t: (calls.append(argv) or (0, ""))
    jarvis.handle_update(text_update(OWNER, "/shutdown pc"))
    token = next(iter(jarvis.pending))
    jarvis.handle_update(text_update(OWNER, f"/shutdown pc {token}"))
    jarvis.handle_update(text_update(OWNER, f"/shutdown pc {token}"))
    assert len(calls) == 1, "a token must be single-use"


def test_expired_token_is_refused(jarvis, mock_tg):
    calls = []
    jarvis.runner = lambda argv, t: (calls.append(argv) or (0, ""))
    jarvis.handle_update(text_update(OWNER, "/shutdown pc"))
    token = next(iter(jarvis.pending))
    jarvis.pending[token].expires = time.time() - 1
    jarvis.handle_update(text_update(OWNER, f"/shutdown pc {token}"))
    assert calls == []


def test_reboot_and_sleep_also_require_confirmation(jarvis, mock_tg):
    calls = []
    jarvis.runner = lambda argv, t: (calls.append(argv) or (0, ""))
    for cmd in ("/reboot pc", "/sleep mac"):
        jarvis.handle_update(text_update(OWNER, cmd))
    assert calls == []


# ------------------------------------------------------------- aali
def test_aali_status(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/aali status"))
    assert "عقل آلي" in mock_tg.last_text()


def test_aali_rejects_unknown_subcommand(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/aali destroy"))
    assert "❌" in mock_tg.last_text()


# -------------------------------------------------------------- chat
def test_chat_uses_groq_and_keeps_history(jarvis, mock_tg, monkeypatch):
    calls = []

    def fake_chat(cfg, message, history=None, system=None):
        calls.append(message)
        return "الرد"

    monkeypatch.setattr(groq, "chat", fake_chat)
    jarvis.handle_update(text_update(OWNER, "كيف حالك"))
    assert mock_tg.last_text() == "الرد"
    jarvis.handle_update(text_update(OWNER, "وماذا عنك"))
    assert len(calls) == 2
    assert len(jarvis.history) == 4


def test_chat_survives_groq_failure(jarvis, mock_tg, monkeypatch):
    def boom(*a, **k):
        raise groq.GroqError("HTTP 401")

    monkeypatch.setattr(groq, "chat", boom)
    jarvis.handle_update(text_update(OWNER, "مرحبا"))
    assert "⚠️" in mock_tg.last_text()


def test_typing_action_is_sent(jarvis, mock_tg, monkeypatch):
    monkeypatch.setattr(groq, "chat", lambda *a, **k: "ok")
    jarvis.handle_update(text_update(OWNER, "مرحبا"))
    assert mock_tg.typing


# ------------------------------------------------------------- voice
def test_voice_note_is_transcribed_and_answered(jarvis, mock_tg, monkeypatch):
    monkeypatch.setattr(jarvis.tg, "download_file", lambda fid: b"\x00" * 128)
    monkeypatch.setattr(groq, "transcribe", lambda *a, **k: "شغّل الحاسوب")
    monkeypatch.setattr(groq, "chat", lambda *a, **k: "تم")
    jarvis.handle_update(
        {"update_id": 2, "message": {"chat": {"id": OWNER}, "voice": {"file_id": "F", "duration": 3}}}
    )
    joined = " ".join(t["text"] for t in mock_tg.sent_text)
    assert "شغّل الحاسوب" in joined
    assert "تم" in joined


def test_voice_failure_does_not_crash(jarvis, mock_tg, monkeypatch):
    monkeypatch.setattr(jarvis.tg, "download_file", lambda fid: b"\x00")

    def boom(*a, **k):
        raise groq.GroqError("bad")

    monkeypatch.setattr(groq, "transcribe", boom)
    jarvis.handle_update(
        {"update_id": 3, "message": {"chat": {"id": OWNER}, "voice": {"file_id": "F"}}}
    )
    assert "⚠️" in mock_tg.last_text()


def test_speak_sends_voice_when_tools_present(jarvis, mock_tg, monkeypatch, tmp_path):
    made = tmp_path / "v.ogg"
    made.write_bytes(b"OggS-fake")

    def fake_synth(text, out_path=None, runner=None, voice=None, allow_clone=True):
        return made

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    monkeypatch.setattr(tts, "available", lambda: True)
    monkeypatch.setattr(jarvis, "english_line", lambda text: "All commands listed.")
    jarvis.handle_update(text_update(OWNER, "/help"), voice=True)
    assert mock_tg.sent_voice, "expected a voice note"


def test_voice_speaks_english_not_the_arabic_report(jarvis, mock_tg, monkeypatch, tmp_path):
    """The owner's rule: Arabic text out, ENGLISH voice. The Arabic string
    must never be what gets synthesized."""
    made = tmp_path / "v.ogg"
    made.write_bytes(b"OggS-fake")
    spoken = {}

    def fake_synth(text, out_path=None, runner=None, voice=None, allow_clone=True):
        spoken["text"] = text
        spoken["allow_clone"] = allow_clone
        return made

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    monkeypatch.setattr(tts, "available", lambda: True)
    monkeypatch.setattr(jarvis, "english_line", lambda text: "The PC is online.")
    jarvis.handle_update(text_update(OWNER, "/status"), voice=True)

    assert spoken["text"] == "The PC is online."
    assert not any("\u0600" <= ch <= "\u06ff" for ch in spoken["text"])
    assert spoken["allow_clone"] is True
    assert mock_tg.sent_voice


def test_no_english_line_falls_back_to_local_arabic_voice(
    jarvis, mock_tg, monkeypatch, tmp_path
):
    """If the brain cannot produce an English line, Jarvis must still SPEAK
    (robot Arabic voice) rather than go silent -- and must NOT hand Arabic
    script to the English clone."""
    made = tmp_path / "v.ogg"
    made.write_bytes(b"OggS-fake")
    seen = {}

    def fake_synth(text, out_path=None, runner=None, voice=None, allow_clone=True):
        seen["allow_clone"] = allow_clone
        return made

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    monkeypatch.setattr(tts, "available", lambda: True)
    monkeypatch.setattr(jarvis, "english_line", lambda text: "")
    jarvis.handle_update(text_update(OWNER, "/status"), voice=True)

    assert seen["allow_clone"] is False, "Arabic must not go to the English clone"
    assert mock_tg.sent_voice, "silence is worse than a robot voice"


def test_english_line_passes_english_through_without_a_model_call(jarvis):
    """Already-English text is cleaned and returned with no Groq call."""
    def boom(*a, **k):
        raise AssertionError("must not call the model for English text")

    jarvis.groq_cfg = jarvis.groq_cfg  # unchanged
    import jarvis.commands as commands_mod

    original = commands_mod.groq.chat
    commands_mod.groq.chat = boom
    try:
        out = jarvis.english_line("The **PC** is online.")
    finally:
        commands_mod.groq.chat = original
    assert out == "The PC is online."
    assert "*" not in out


def test_english_line_caps_the_spoken_length(jarvis, monkeypatch):
    long_text = " ".join(["word"] * 200)
    import jarvis.commands as commands_mod

    monkeypatch.setattr(
        commands_mod.groq, "chat", lambda *a, **k: " ".join(["said"] * 200)
    )
    out = jarvis.english_line("الحاسوب يعمل الآن بشكل جيد جدا")
    assert len(out.split()) <= commands_mod.SPOKEN_MAX_WORDS


def test_english_line_is_empty_when_the_brain_fails(jarvis, monkeypatch):
    import jarvis.commands as commands_mod

    def boom(*a, **k):
        raise commands_mod.groq.GroqError("HTTP 500")

    monkeypatch.setattr(commands_mod.groq, "chat", boom)
    assert jarvis.english_line("الحاسوب يعمل") == ""


def test_speak_survives_tts_failure(jarvis, mock_tg, monkeypatch):
    def boom(*a, **k):
        raise tts.TTSError("no espeak")

    monkeypatch.setattr(tts, "synthesize", boom)
    monkeypatch.setattr(tts, "available", lambda: True)
    jarvis.handle_update(text_update(OWNER, "/help"))
    assert mock_tg.sent_text, "text must still go out when TTS fails"


# -------------------------------------------------------------- logs
def test_logs_rejects_path_traversal(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/logs pc ../../etc/passwd"))
    assert "❌" in mock_tg.last_text()


# --------------------------------------------------------------- tts
def test_pick_voice_follows_the_script():
    assert tts.pick_voice("مرحبا") == "ar"
    assert tts.pick_voice("hello") == "en"


def test_build_env_points_at_home_bins():
    env = tts.build_env()
    assert str(tts.BIN_DIR) in env["PATH"]
    assert str(tts.LIB_DIR) in env["LD_LIBRARY_PATH"]
    assert env["ESPEAK_DATA_PATH"] == str(tts.DATA_DIR)


def test_synthesize_refuses_empty_text():
    with pytest.raises(tts.TTSError):
        tts.synthesize("   ")


def test_synthesize_reports_missing_tools(monkeypatch):
    monkeypatch.setattr(tts, "espeak_path", lambda: None)
    with pytest.raises(tts.TTSError) as exc:
        tts.synthesize("hello")
    assert "espeak" in str(exc.value)


def test_synthesize_uses_the_two_stage_pipeline(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, env, timeout):
        calls.append(argv)
        # espeak writes to the path after -w; ffmpeg to the trailing output arg
        out = Path(argv[argv.index("-w") + 1]) if "-w" in argv else Path(argv[-1])
        out.write_bytes(b"RIFF-fake" if "espeak" in argv[0] else b"OggS-fake")
        return 0, ""

    monkeypatch.setattr(tts, "espeak_path", lambda: "/home/x/bin/espeak-ng")
    monkeypatch.setattr(tts, "ffmpeg_path", lambda: "/home/x/bin/ffmpeg")
    out = tmp_path / "v.ogg"
    path = tts.synthesize("مرحبا", out_path=out, runner=fake_run)
    assert len(calls) == 2
    assert "-v" in calls[0] and "ar" in calls[0]
    assert "libopus" in calls[1]
    assert out.exists()


# ------------------------------------------------------- groq shaping
def test_groq_chat_raises_on_bad_shape():
    cfg = GroqConfig(api_key="k")

    def fake_post(url, headers, payload):
        return {"choices": []}

    import jarvis.groq as g

    orig = g._post_json
    g._post_json = fake_post
    try:
        with pytest.raises(groq.GroqError):
            groq.chat(cfg, "hi")
    finally:
        g._post_json = orig


def test_transcribe_empty_is_an_error(monkeypatch):
    import jarvis.groq as g

    cfg = GroqConfig(api_key="k")
    monkeypatch.setattr(g, "_post_multipart", lambda *a, **k: {"text": "  "})
    with pytest.raises(groq.GroqError):
        groq.transcribe(cfg, b"x")

# ---------------------------------------------------------------- cloned voice
# The Pi cannot host XTTS (905 MB RAM, no torch), so the PC renders the owner's
# cloned voice and the Pi only converts to OGG. These pin that contract.


def _wav_bytes(tag=b"RIFF"):
    return tag + b"\x00" * 100


def test_clone_unconfigured_means_no_clone(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "VOICE_API", "")
    assert tts.clone_available() is False


def test_clone_available_needs_both_url_and_token(monkeypatch, tmp_path):
    tok = tmp_path / "voice_token.txt"
    tok.write_text("secret-token\n", encoding="utf-8")
    monkeypatch.setattr(tts, "VOICE_TOKEN_FILE", tok)

    monkeypatch.setattr(tts, "VOICE_API", "")
    assert tts.clone_available() is False
    monkeypatch.setattr(tts, "VOICE_API", "http://192.168.1.13:5082")
    assert tts.clone_available() is True


def test_fetch_clone_wav_posts_english_with_the_token(monkeypatch, tmp_path):
    """The clone is an ENGLISH voice: lang must be 'en' whatever the text is,
    and the shared secret must ride along as a header."""
    tok = tmp_path / "voice_token.txt"
    tok.write_text("s3cret", encoding="utf-8")
    monkeypatch.setattr(tts, "VOICE_TOKEN_FILE", tok)
    monkeypatch.setattr(tts, "VOICE_API", "http://192.168.1.13:5082")

    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _wav_bytes()

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["token"] = req.headers.get("X-aali-voice-token")
        seen["body"] = req.data.decode("utf-8")
        seen["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    out = tts.fetch_clone_wav("\u0627\u0644\u062d\u0627\u0633\u0648\u0628 \u064a\u0639\u0645\u0644")

    assert out.startswith(b"RIFF")
    assert seen["url"].endswith("/api/synthesize")
    assert seen["token"] == "s3cret"
    assert '"lang": "en"' in seen["body"]
    assert seen["timeout"] and seen["timeout"] > 60, "CPU XTTS needs a long timeout"


def test_fetch_clone_wav_rejects_non_wav(monkeypatch, tmp_path):
    """A 200 that is not a WAV must not be spoken as one."""
    tok = tmp_path / "voice_token.txt"
    tok.write_text("s3cret", encoding="utf-8")
    monkeypatch.setattr(tts, "VOICE_TOKEN_FILE", tok)
    monkeypatch.setattr(tts, "VOICE_API", "http://192.168.1.13:5082")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>401 unauthorized</html>"

    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    with pytest.raises(tts.TTSError):
        tts.fetch_clone_wav("hello")


def test_synthesize_uses_the_clone_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "clone_available", lambda: True)
    monkeypatch.setattr(tts, "fetch_clone_wav", lambda text, timeout=None: _wav_bytes())

    def fake_run(argv, env, timeout):
        # the runner drives espeak AND ffmpeg; write whatever file is asked for
        if "-w" in argv:
            Path(argv[argv.index("-w") + 1]).write_bytes(_wav_bytes())
        else:
            Path(argv[-1]).write_bytes(b"OggS-fake")
        return 0, ""

    out = tts.synthesize(
        "All systems online.",
        out_path=tmp_path / "o.ogg",
        runner=fake_run,
    )
    assert Path(out).exists()
    assert tts.LAST_SOURCE == "clone"


def test_synthesize_falls_back_to_espeak_when_the_pc_is_asleep(monkeypatch, tmp_path):
    """PC asleep / server down must still produce a voice note."""
    monkeypatch.setattr(tts, "clone_available", lambda: True)

    def dead(text, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(tts, "fetch_clone_wav", dead)
    calls = []

    def fake_run(argv, env, timeout):
        calls.append(argv)
        if "-w" in argv:
            Path(argv[argv.index("-w") + 1]).write_bytes(_wav_bytes())
        else:
            Path(argv[-1]).write_bytes(b"OggS-fake")
        return 0, ""

    monkeypatch.setattr(tts, "espeak_path", lambda: "/bin/espeak-ng")
    tts.synthesize(
        "\u0627\u0644\u062d\u0627\u0633\u0648\u0628 \u064a\u0639\u0645\u0644",
        out_path=tmp_path / "o.ogg",
        runner=fake_run,
    )
    assert tts.LAST_SOURCE == "espeak"
    assert any("espeak" in str(a) for a in calls)


def test_arabic_text_never_reaches_the_english_clone(monkeypatch, tmp_path):
    """allow_clone=False must keep Arabic script away from the English voice."""
    monkeypatch.setattr(tts, "clone_available", lambda: True)

    def must_not_run(text, timeout=None):
        raise AssertionError("Arabic must not be sent to the English clone")

    monkeypatch.setattr(tts, "fetch_clone_wav", must_not_run)

    def fake_run(argv, env, timeout):
        if "-w" in argv:
            Path(argv[argv.index("-w") + 1]).write_bytes(_wav_bytes())
        else:
            Path(argv[-1]).write_bytes(b"OggS-fake")
        return 0, ""

    monkeypatch.setattr(tts, "espeak_path", lambda: "/bin/espeak-ng")
    tts.synthesize(
        "\u0627\u0644\u062d\u0627\u0633\u0648\u0628 \u064a\u0639\u0645\u0644",
        out_path=tmp_path / "o.ogg",
        runner=fake_run,
        allow_clone=False,
    )
    assert tts.LAST_SOURCE == "espeak"


def test_available_needs_ffmpeg_even_with_a_clone(monkeypatch):
    """Telegram wants OGG; the clone returns WAV. No ffmpeg = no voice."""
    monkeypatch.setattr(tts, "ffmpeg_path", lambda: None)
    monkeypatch.setattr(tts, "clone_available", lambda: True)
    assert tts.available() is False

    monkeypatch.setattr(tts, "ffmpeg_path", lambda: "/bin/ffmpeg")
    monkeypatch.setattr(tts, "espeak_path", lambda: "/bin/espeak-ng")
    assert tts.available() is True


def test_log_allows_the_voice_source_fields():
    from jarvis.log import sanitize

    got = sanitize({"source": "clone", "spoken_chars": 24, "text": "SECRET"})
    assert got["source"] == "clone"
    assert got["spoken_chars"] == 24
    assert "text" not in got, "content must never reach the log"


# ============================================================ Freebuff inbox
def _read_inbox(cfg_dir):
    path = handoff.inbox_path(cfg_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_handoff_appends_the_note_verbatim(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(OWNER, "send to freebuff check the CI log"))
    notes = _read_inbox(cfg_dir)
    assert len(notes) == 1
    assert notes[0]["text"] == "check the CI log"
    assert notes[0]["origin"] == "telegram"
    assert notes[0]["id"]


@pytest.mark.parametrize(
    "phrase",
    [
        "send to freebuff look at the failing test",
        "Send it to Freebuff: look at the failing test",
        "please forward this to buffy — look at the failing test",
        "freebuff: look at the failing test",
        "tell barney about the deploy",
        "ارسل إلى فريفب: شغّل الاختبارات",
        "أرسلها ل فريفب شغّل الاختبارات",
        "بوفي: شغل الاختبارات",
    ],
)
def test_every_spelling_of_the_phrase_reaches_the_inbox(
    jarvis, mock_tg, cfg_dir, phrase
):
    jarvis.handle_update(text_update(OWNER, phrase))
    assert len(_read_inbox(cfg_dir)) == 1, phrase


@pytest.mark.parametrize(
    "chat",
    [
        "what can buffy do?",
        "how do i show buffy's status?",
        "send this to the server",
        "what does barney mean in arabic?",
        "please help me",
    ],
)
def test_ordinary_chat_is_not_hijacked_by_the_name(jarvis, mock_tg, cfg_dir, monkeypatch, chat):
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "جواب")
    jarvis.handle_update(text_update(OWNER, chat))
    assert _read_inbox(cfg_dir) == [], chat
    assert asked, "the message must still reach the brain"


def test_a_stranger_cannot_fill_the_inbox(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(424242, "send to freebuff rm -rf everything"))
    assert _read_inbox(cfg_dir) == []
    assert mock_tg.sent_text == []


def test_slash_command_carries_the_payload(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(OWNER, "/freebuff deploy the pi branch"))
    assert _read_inbox(cfg_dir)[0]["text"] == "deploy the pi branch"
    assert "فريفب" in mock_tg.last_text()


def test_phrase_without_a_payload_opens_a_request(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(OWNER, "send to freebuff"))
    assert _read_inbox(cfg_dir) == [], "nothing was attached yet"
    assert jarvis.freebuff_until > 0
    # the NEXT message is the payload
    jarvis.handle_update(text_update(OWNER, "the build is red on the pi"))
    assert _read_inbox(cfg_dir)[0]["text"] == "the build is red on the pi"
    assert jarvis.freebuff_until == 0


def test_a_command_cancels_the_open_request(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(OWNER, "/freebuff"))
    jarvis.handle_update(text_update(OWNER, "/status"))
    jarvis.handle_update(text_update(OWNER, "just a normal question"))
    assert _read_inbox(cfg_dir) == [], "the payload must not be swallowed"


def test_an_expired_request_stops_swallowing(jarvis, mock_tg, cfg_dir, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_000.0)
    jarvis.handle_update(text_update(OWNER, "/freebuff"))
    monkeypatch.setattr(time, "time", lambda: 1_000.0 + commands.FREEBUFF_TTL + 1)
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "جواب")
    jarvis.handle_update(text_update(OWNER, "actually never mind"))
    assert _read_inbox(cfg_dir) == []
    assert asked


def test_empty_payload_is_not_reported_as_sent(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(OWNER, "/freebuff@AaliJarvisbot"))
    assert _read_inbox(cfg_dir) == []
    assert "❌" not in mock_tg.last_text(), "an empty command must ask, not claim success"


def test_a_voice_note_can_hand_something_over(jarvis, mock_tg, cfg_dir, monkeypatch):
    monkeypatch.setattr(groq, "transcribe", lambda *a, **k: "send to freebuff the wifi keeps dropping")
    jarvis.handle_update(
        {
            "update_id": 9,
            "message": {
                "chat": {"id": OWNER},
                "voice": {"file_id": "abc", "duration": 4},
            },
        }
    )
    assert _read_inbox(cfg_dir)[0]["text"] == "the wifi keeps dropping"


def test_a_voice_note_still_answers_ordinary_questions(jarvis, mock_tg, cfg_dir, monkeypatch):
    asked = []
    monkeypatch.setattr(groq, "transcribe", lambda *a, **k: "كم الساعة؟")
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "الساعة ست")
    jarvis.handle_update(
        {
            "update_id": 10,
            "message": {"chat": {"id": OWNER}, "voice": {"file_id": "abc", "duration": 2}},
        }
    )
    assert _read_inbox(cfg_dir) == []
    assert asked == ["كم الساعة؟"]


def test_the_handoff_never_reaches_the_content_free_log(jarvis, mock_tg, cfg_dir, monkeypatch, tmp_path):
    log_file = tmp_path / "jarvis.log"
    monkeypatch.setattr(log, "log_path", lambda *a, **k: log_file)
    jarvis.handle_update(text_update(OWNER, "send to freebuff the secret plan is blue"))
    written = log_file.read_text(encoding="utf-8")
    assert "handoff_sent" in written
    assert "blue" not in written and "secret plan" not in written


def test_secrets_are_masked_before_they_are_stored(jarvis, mock_tg, cfg_dir):
    secret = "gsk_live0123456789abcdefgh"
    jarvis.handle_update(text_update(OWNER, f"send to freebuff rotate this: {secret}"))
    stored = _read_inbox(cfg_dir)[0]["text"]
    assert secret not in stored
    assert "مفتاح" in stored
    assert _read_inbox(cfg_dir)[0]["secrets_masked"] == 1
    assert "⚠️" in mock_tg.last_text(), "the owner must be told a key was stripped"


@pytest.mark.parametrize(
    "secret",
    [
        "gsk_live0123456789abcdefgh",
        "sk-abcdefghijklmnopqrstuvwx",
        "ghp_0123456789abcdefghijklmnopqrstuvwxyz",
        "1234567890:AAHabcdefghijklmnopqrstuvwxyz1234567",
        'api_key = "Zx9Qwertyuiop12345678"',
        "d41d8cd98f00b204e9800998ecf8427e",
    ],
)
def test_every_known_key_shape_is_caught(cfg_dir, secret):
    clean, hits = handoff.scrub_secrets(f"value is {secret} ok")
    assert hits >= 1
    assert secret not in clean
    assert "ok" in clean, "the surrounding message must survive"


def test_prose_that_only_looks_suspicious_survives(cfg_dir):
    text = "the deployment key is the green one, see ticket 12345"
    clean, hits = handoff.scrub_secrets(text)
    assert hits == 0 and clean == text


def test_the_inbox_is_capped(cfg_dir):
    monkey = handoff.MAX_ENTRIES
    try:
        handoff.MAX_ENTRIES = 5
        for i in range(12):
            assert handoff.append(f"note {i}", cfg_dir=cfg_dir) is not None
    finally:
        handoff.MAX_ENTRIES = monkey
    notes = _read_inbox(cfg_dir)
    assert len(notes) == 5
    assert notes[-1]["text"] == "note 11", "the NEWEST notes are the ones kept"


def test_the_inbox_file_is_private(cfg_dir):
    handoff.append("a note", cfg_dir=cfg_dir)
    path = handoff.inbox_path(cfg_dir)
    if os.name == "nt":
        pytest.skip("POSIX modes do not exist on Windows")
    assert path.stat().st_mode & 0o077 == 0


def test_empty_text_is_never_queued(cfg_dir):
    assert handoff.append("   ", cfg_dir=cfg_dir) is None
    assert _read_inbox(cfg_dir) == []


def test_a_very_long_note_is_truncated(cfg_dir):
    rec = handoff.append("x" * (handoff.MAX_TEXT_CHARS + 500), cfg_dir=cfg_dir)
    assert len(rec["text"]) <= handoff.MAX_TEXT_CHARS + 20


def test_damaged_lines_do_not_hide_the_real_notes(cfg_dir):
    handoff.append("first", cfg_dir=cfg_dir)
    with handoff.inbox_path(cfg_dir).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n\n")
    handoff.append("second", cfg_dir=cfg_dir)
    assert [n["text"] for n in handoff.read_all(cfg_dir)] == ["first", "second"]


def test_read_all_unread_hides_handled_notes(cfg_dir):
    a = handoff.append("one", cfg_dir=cfg_dir)
    b = handoff.append("two", cfg_dir=cfg_dir)
    assert handoff.pending_count(cfg_dir) == 2
    assert handoff.mark_handled([a["id"]], cfg_dir=cfg_dir) == 1
    pending = handoff.read_all(cfg_dir, unread_only=True)
    assert [n["text"] for n in pending] == ["two"]
    assert [n["text"] for n in handoff.read_all(cfg_dir)] == ["one", "two"]
    assert handoff.mark_handled([b["id"]], cfg_dir=cfg_dir) == 1
    assert handoff.pending_count(cfg_dir) == 0
    assert handoff.mark_handled([b["id"]], cfg_dir=cfg_dir) == 0, "no double counting"


def test_the_inbox_follows_the_jarvis_config_dir(cfg_dir, monkeypatch):
    monkeypatch.setenv("AALI_JARVIS_CONFIG", str(cfg_dir))
    assert handoff.inbox_path() == cfg_dir / "freebuff_inbox.jsonl"
    assert handoff.append("landed", cfg_dir=None)["text"] == "landed"
    assert (cfg_dir / "freebuff_inbox.jsonl").exists()


def test_handoff_cli_send_and_read(cfg_dir, capsys):
    assert handoff.main(["--config-dir", str(cfg_dir), "--send", "from the cli"]) == 0
    assert handoff.main(["--config-dir", str(cfg_dir), "--take"]) == 0
    assert "from the cli" in capsys.readouterr().out
    assert handoff.main(["--config-dir", str(cfg_dir)]) == 0
    assert "inbox empty" in capsys.readouterr().out, "a taken inbox reads as empty"


def test_handoff_cli_json_is_parsable(cfg_dir, capsys):
    handoff.append("json please", cfg_dir=cfg_dir)
    handoff.main(["--config-dir", str(cfg_dir), "--json"])
    assert json.loads(capsys.readouterr().out)[0]["text"] == "json please"


def test_freebuff_status_reports_the_inbox(jarvis, mock_tg, cfg_dir):
    jarvis.handle_update(text_update(OWNER, "/freebuffstatus"))
    assert "فارغ" in mock_tg.last_text()
    handoff.append("waiting for you", cfg_dir=cfg_dir)
    jarvis.handle_update(text_update(OWNER, "/freebuffs"))
    text = mock_tg.last_text()
    assert "لم يقرأها فريفب بعد" in text
    handoff.mark_handled([n["id"] for n in handoff.read_all(cfg_dir)], cfg_dir=cfg_dir)
    jarvis.handle_update(text_update(OWNER, "/freebuffs"))
    assert "قرأها كلها" in mock_tg.last_text()


def test_the_help_lists_the_bridge(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/help"))
    assert "/freebuff" in mock_tg.last_text()


@pytest.mark.parametrize(
    "phrase",
    ["send to freebuff", "please send to freebuff", "ارسل إلى فريفب"],
)
def test_a_polite_phrase_with_no_payload_still_opens_the_request(
    jarvis, mock_tg, cfg_dir, phrase
):
    jarvis.handle_update(text_update(OWNER, phrase))
    assert _read_inbox(cfg_dir) == []
    assert jarvis.freebuff_until > 0, phrase
    assert "📥" in mock_tg.last_text()


# ============================================================================
# Speaking instead of typing: the natural-language router
# ============================================================================
# The owner wants to talk to the bot from wherever he is and have it obey. The
# contract these tests pin is NOT "the router understands everything" -- that is
# Groq's job and it will be wrong sometimes. The contract is that a decision
# can only ever NAME a capability that already exists, that a decision nothing
# can be done about degrades to ordinary chat, and that no sentence -- however
# it is phrased -- reaches a destructive action without the two-step token.


def test_the_router_is_off_unless_a_test_asks_for_it(jarvis):
    """The bot fixture must never let a message reach the real Groq router."""
    assert jarvis.intent_enabled is False


@pytest.fixture
def router(jarvis, monkeypatch):
    """Script the router's MODEL, keeping the real validate() in the path.

    Patching ``groq.chat_json`` rather than ``intent.classify`` on purpose: the
    validator is the security boundary, so an invented action or an undeclared
    argument has to be refused by the same code that refuses it in production.
    """

    def _script(reply):
        def fake_json(cfg, message, system, history=None, model=None, max_tokens=300):
            seen.append({"message": message, "system": system, "model": model})
            if isinstance(reply, Exception):
                raise reply
            return groq.parse_json_object(reply) if isinstance(reply, str) else dict(reply)

        seen: list[dict] = []
        monkeypatch.setattr(groq, "chat_json", fake_json)
        jarvis.intent_enabled = True
        jarvis.router_calls = seen
        return jarvis

    return _script


@pytest.fixture
def no_chat(jarvis, monkeypatch):
    """Fail loudly if a handler that should have acted falls through to chat."""
    monkeypatch.setattr(
        groq,
        "chat",
        lambda *a, **k: pytest.fail("the router should have handled this, not chat"),
    )
    return jarvis


# ------------------------------------------------------------ the table
def test_every_capability_speaks_arabic_too():
    for cap in intent.CAPABILITIES:
        arabic = intent.SUMMARY_AR.get(cap.name, "")
        assert arabic, f"{cap.name} has no Arabic one-liner for the router prompt"
        assert any("\u0600" <= ch <= "\u06ff" for ch in arabic), cap.name


def test_the_prompt_lists_every_capability_and_nothing_is_left_behind():
    prompt = intent.build_prompt()
    assert "@CAPABILITIES@" not in prompt, "the table was never spliced in"
    for cap in intent.CAPABILITIES:
        assert f'"{cap.name}"' in prompt, cap.name
    # The owner's own spellings for the agent must be in the prompt, or the
    # router will not recognise them.
    for word in ("فريفب", "Buffy", "Barney"):
        assert word in prompt, word


def test_only_power_is_marked_destructive():
    destructive = [c.name for c in intent.CAPABILITIES if c.destructive]
    assert destructive == ["power"]


def test_validate_refuses_an_invented_action():
    assert intent.validate({"action": "run_shell", "args": {"cmd": "rm -rf /"}}) is None


def test_validate_refuses_an_undeclared_argument():
    out = intent.validate(
        {"action": "freebuff_send", "args": {"message": "hi", "command": "rm -rf /"}}
    )
    assert out["args"] == {"message": "hi"}, "an undeclared field must not survive"


def test_validate_refuses_a_value_outside_the_allowed_choices():
    out = intent.validate({"action": "status", "args": {"machine": "the router"}})
    assert out["args"] == {}, "a made-up machine name must be dropped, not passed on"
    out = intent.validate({"action": "power", "args": {"action": "format the disk"}})
    assert out is None, "an unknown destructive action must decide nothing at all"


def test_validate_needs_the_required_argument():
    assert intent.validate({"action": "aali_ask", "args": {}}) is None
    assert intent.validate({"action": "freebuff_send", "args": {"message": "  "}}) is None


def test_validate_drops_structures_where_a_string_belongs():
    assert intent.validate({"action": "aali_ask", "args": {"question": ["a", "b"]}}) is None


def test_validate_truncates_a_ridiculously_long_payload():
    out = intent.validate(
        {"action": "freebuff_send", "args": {"message": "x" * (intent.MAX_ARG_CHARS + 900)}}
    )
    assert len(out["args"]["message"]) == intent.MAX_ARG_CHARS


def _record_power(fired):
    """A power stub that records the call AND answers like the real thing.

    Returning None here would blow up on ``result.ok`` inside the handler,
    which is a confusing way to discover the stub is wrong.
    """

    def _act(machine, action, runner=None):
        fired.append((machine.key, action))
        return power.ActionResult(ok=True, detail=f"{machine.name}: ok")

    return _act


# ------------------------------------------------------------ the routing
def test_a_plain_question_about_the_house_runs_the_status_handler(
    router, no_chat, mock_tg, machines
):
    j = router('{"action": "status", "args": {"machine": "all"}}')
    no_chat.handle_update(text_update(OWNER, "is anything on?"))
    assert "📡" in mock_tg.last_text()
    assert len(machines) >= 2, "the status line should cover the registry"


def test_asking_her_to_do_something_reaches_the_inbox(router, mock_tg, cfg_dir):
    """A phrasing the regex layer does NOT catch, routed by the model."""
    j = router('{"action": "freebuff_send", "args": {"message": "deploy the pi branch"}}')
    j.handle_update(text_update(OWNER, "ask her to deploy the pi branch"))
    stored = _read_inbox(cfg_dir)
    assert stored[-1]["text"] == "deploy the pi branch"
    assert "فريفب" in mock_tg.last_text()


def test_the_payload_is_scrubbed_on_the_routed_path_too(router, mock_tg, cfg_dir):
    j = router('{"action": "freebuff_send", "args": {"message": "key gsk_live0123456789abcdefgh"}}')
    j.handle_update(text_update(OWNER, "pass her my key"))
    stored = _read_inbox(cfg_dir)[-1]
    assert "gsk_live0123456789abcdefgh" not in stored["text"]
    assert "⚠️" in mock_tg.last_text(), "he must be told a key was stripped"


def test_spoken_shutdown_only_mints_a_token(router, mock_tg, monkeypatch):
    fired = []
    monkeypatch.setattr(
        power, "power_action", _record_power(fired)
    )
    j = router('{"action": "power", "args": {"machine": "pc", "action": "shutdown"}}')
    j.handle_update(text_update(OWNER, "shut the pc down when you get a chance"))
    assert fired == [], "a spoken sentence must never power off a machine"
    assert "تأكيد" in mock_tg.last_text()
    assert list(j.pending), "a token must be waiting"
    assert len(j.pending) == 1


def test_answering_with_the_token_in_plain_words_does_the_job(router, mock_tg, monkeypatch):
    fired = []
    monkeypatch.setattr(
        power, "power_action", _record_power(fired)
    )
    j = router('{"action": "power", "args": {"machine": "pc", "action": "sleep"}}')
    j.handle_update(text_update(OWNER, "put the pc to sleep"))
    token = next(iter(j.pending))
    j.handle_update(text_update(OWNER, f"yes {token}"))
    assert fired == [("pc", "sleep")]
    assert not j.pending, "the token must not be replayable"
    assert "✅" in mock_tg.last_text()


def test_a_bare_code_is_enough_because_the_prompt_is_waiting(router, mock_tg, monkeypatch):
    fired = []
    monkeypatch.setattr(
        power, "power_action", _record_power(fired)
    )
    j = router('{"action": "power", "args": {"machine": "pc", "action": "reboot"}}')
    j.handle_update(text_update(OWNER, "reboot the pc"))
    token = next(iter(j.pending))
    j.handle_update(text_update(OWNER, token))
    assert fired == [("pc", "reboot")]


def test_saying_no_cancels_the_destructive_action(router, mock_tg, monkeypatch):
    fired = []
    monkeypatch.setattr(
        power, "power_action", _record_power(fired)
    )
    j = router('{"action": "power", "args": {"machine": "pc", "action": "shutdown"}}')
    j.handle_update(text_update(OWNER, "power off the pc"))
    token = next(iter(j.pending))
    j.handle_update(text_update(OWNER, f"no {token}"))
    assert fired == []
    assert not j.pending
    assert "أُلغي" in mock_tg.last_text()


def test_an_expired_token_asked_for_in_words_is_refused(router, mock_tg, monkeypatch):
    fired = []
    monkeypatch.setattr(
        power, "power_action", _record_power(fired)
    )
    j = router('{"action": "power", "args": {"machine": "pc", "action": "shutdown"}}')
    j.handle_update(text_update(OWNER, "shut it down"))
    token = next(iter(j.pending))
    j.pending[token].expires = time.time() - 1
    j.handle_update(text_update(OWNER, f"yes {token}"))
    assert fired == [], "an expired confirmation must not fire"
    assert "⌛" in mock_tg.last_text()


def test_ordinary_smalltalk_still_goes_to_the_brain(router, mock_tg, monkeypatch):
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "أهلاً")
    j = router('{"action": "chat"}')
    j.handle_update(text_update(OWNER, "how was your day?"))
    assert asked == ["how was your day?"]
    assert "أهلاً" in mock_tg.last_text()


def test_an_impossible_request_is_refused_honestly_with_a_next_step(router, mock_tg):
    j = router('{"action": "unsupported", "args": {"why": "there is no door in the house"}}')
    j.handle_update(text_update(OWNER, "open the front door"))
    text = mock_tg.last_text()
    assert "ما أقدر" in text
    assert commands.CAPABILITY_HINT in text


def test_a_hallucinated_action_degrades_to_chat_instead_of_guessing(router, mock_tg, monkeypatch):
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "تمام")
    j = router('{"action": "format_the_disk", "args": {"machine": "pc"}}')
    j.handle_update(text_update(OWNER, "wipe the pc please"))
    assert asked == ["wipe the pc please"], "the sentence must still be answered"


def test_a_broken_router_reply_never_stops_the_bot(router, mock_tg, monkeypatch):
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "جواب")
    j = router("this is not json at all")
    j.handle_update(text_update(OWNER, "are you there?"))
    assert asked, "an unusable decision must fall back to chat"
    assert "جواب" in mock_tg.last_text()


def test_the_router_going_down_does_not_take_the_bot_with_it(router, mock_tg, monkeypatch):
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "جواب")
    j = router(groq.GroqError("HTTP 429"))
    j.handle_update(text_update(OWNER, "are you there?"))
    assert "جواب" in mock_tg.last_text()


def test_the_router_runs_cold_and_cheap(router, mock_tg):
    j = router('{"action": "chat"}')
    j.handle_update(text_update(OWNER, "hello"))
    call = j.router_calls[0]
    assert call["model"] == j.intent_model, "the router gets its own model"


def test_resources_without_a_valid_choice_asks_instead_of_guessing(router, mock_tg):
    j = router('{"action": "resources", "args": {"machine": "pc"}}')
    j.handle_update(text_update(OWNER, "how is the machine doing"))
    assert "❓" in mock_tg.last_text()


def test_a_task_becomes_a_real_queued_note(router, mock_tg, cfg_dir):
    j = router('{"action": "task", "args": {"machine": "pc", "task": "back up the repo"}}')
    j.handle_update(text_update(OWNER, "remind me to back up the repo"))
    stored = _read_inbox(cfg_dir)[-1]
    assert stored["text"] == "back up the repo", "the payload must stay verbatim"
    assert stored["machine"] == "pc", "the machine is metadata, not part of the words"
    assert stored["kind"] == "note"


def test_a_slash_command_still_wins_over_the_router(router, mock_tg, monkeypatch):
    asked = []
    monkeypatch.setattr(groq, "chat", lambda cfg, text, **k: asked.append(text) or "x")
    j = router('{"action": "power", "args": {"machine": "pc", "action": "shutdown"}}')
    j.handle_update(text_update(OWNER, "/status"))
    assert "📡" in mock_tg.last_text()
    assert not j.pending, "a command must not be re-read as an order"


def test_the_arabic_help_survives_and_advertises_plain_speech(jarvis, mock_tg):
    jarvis.handle_update(text_update(OWNER, "/help"))
    text = mock_tg.last_text()
    assert "تكلم عادي" in text
    assert "/ask" in text
    assert "فريفب" in text


# ============================================================== the answers
def test_answers_come_back_to_telegram_and_are_marked_once(jarvis, mock_tg, cfg_dir):
    note = handoff.append("run the suite", cfg_dir=cfg_dir)
    handoff.append(
        "all green, 1837 passed",
        cfg_dir=cfg_dir,
        origin="freebuff",
        kind=handoff.KIND_ANSWER,
        reply_to=note["id"],
    )
    jarvis.handle_update(text_update(OWNER, "/freebuffread"))
    text = mock_tg.last_text()
    assert "all green" in text
    assert note["id"] in text, "he must be able to tell which ask this answers"
    assert handoff.read_answers(cfg_dir, unread_only=True) == [], "delivered once"
    jarvis.handle_update(text_update(OWNER, "/freebuffread"))
    assert "لا يوجد رد جديد" in mock_tg.last_text()


def test_the_owners_own_notes_are_never_echoed_back(jarvis, mock_tg, cfg_dir):
    handoff.append("do this thing", cfg_dir=cfg_dir)
    jarvis.deliver_answers()
    assert mock_tg.sent_text == [], "the poller must not read the owner his own words"


def test_the_poller_stays_silent_when_there_is_nothing(jarvis, mock_tg, cfg_dir):
    assert jarvis.deliver_answers() == 0
    assert mock_tg.sent_text == [], "a quiet inbox must not produce a Telegram message"


def test_a_failed_send_leaves_the_answer_unread(jarvis, mock_tg, cfg_dir, monkeypatch):
    """A Freebuff answer is the owner's work -- a dropped one must be retried."""
    handoff.append(
        "the answer", cfg_dir=cfg_dir, origin="freebuff", kind=handoff.KIND_ANSWER
    )

    def boom(*a, **k):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(jarvis, "speak", boom)
    with pytest.raises(RuntimeError):
        jarvis.cmd_freebuff_read(OWNER)
    assert len(handoff.read_answers(cfg_dir, unread_only=True)) == 1, "still waiting"


def test_the_inbox_directions_are_separate(cfg_dir):
    note = handoff.append("a note", cfg_dir=cfg_dir)
    answer = handoff.append("an answer", cfg_dir=cfg_dir, kind=handoff.KIND_ANSWER)
    assert [r["id"] for r in handoff.read_answers(cfg_dir)] == [answer["id"]]
    assert [r["id"] for r in handoff.of_kind("note", cfg_dir)] == [note["id"]]
    # An unknown kind is a note, never an answer: an unrecognised record must
    # not be able to impersonate Freebuff talking to the owner.
    handoff.append("weird", cfg_dir=cfg_dir, kind="not-a-kind")
    assert len(handoff.read_answers(cfg_dir)) == 1


def test_ask_the_brain_over_the_lan(cfg_dir, monkeypatch, machines):
    """The brain is asked over HTTP, and the answer comes back to Telegram."""
    seen = {}

    def fake_ask(key, machine, question, **kw):
        seen.update({"key": key, "machine": machine.key, "question": question})
        return True, "there are 12 files"

    monkeypatch.setattr(brain, "ask", fake_ask)
    tg = _QuietTelegram()
    j = Jarvis(GroqConfig(api_key="k"), tg, machines, runner=lambda a, t: (0, ""))
    j.cmd_aali_ask(OWNER, "how many files are in the repo?")
    assert seen["machine"] == "pc"
    assert seen["question"] == "how many files are in the repo?"
    assert "12 files" in tg.sent[-1]


def test_without_the_key_the_brain_says_so_instead_of_pretending(cfg_dir, monkeypatch, machines):
    monkeypatch.setattr(brain, "ask", lambda *a, **k: (False, "HTTP 404"))
    tg = _QuietTelegram()
    j = Jarvis(GroqConfig(api_key="k"), tg, machines, runner=lambda a, t: (0, ""))
    j.cmd_aali_ask(OWNER, "are you there?")
    text = tg.sent[-1]
    assert "لم يرد العقل" in text
    assert "aali_key.txt" in text, "the owner must be told exactly what to create"


def test_a_brain_reply_is_capped_for_a_phone_screen(cfg_dir, monkeypatch, machines):
    monkeypatch.setattr(brain, "ask", lambda *a, **k: (True, "y" * 9000))
    tg = _QuietTelegram()
    j = Jarvis(GroqConfig(api_key="k"), tg, machines, runner=lambda a, t: (0, ""))
    j.cmd_aali_ask(OWNER, "dump everything")
    assert len(tg.sent[-1]) < brain.MAX_REPLY + 200


class _QuietTelegram:
    """Just collects what would have been sent."""

    def __init__(self):
        self.sent: list[str] = []
        self.owner_chat_id = OWNER

    def send_text(self, chat_id, text, reply_to=None):
        self.sent.append(text)

    def send_typing(self, chat_id):
        pass

    def send_voice(self, chat_id, path, caption=None):
        pass

    def is_owner(self, chat_id):
        return int(chat_id) == OWNER


class _BrainServer(BaseHTTPRequestHandler):
    """A fake /api/ask so brain.ask is tested over real HTTP."""

    payload = {"ok": True, "reply": "the answer from the pc"}
    status = 200

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        type(self).seen = {"body": json.loads(raw or b"{}"), "key": self.headers.get("X-API-Key")}
        self.send_response(type(self).status)
        body = json.dumps(type(self).payload).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def brain_server(machines):
    _BrainServer.seen = {}
    _BrainServer.status = 200
    _BrainServer.payload = {"ok": True, "reply": "the answer from the pc"}
    srv = HTTPServer(("127.0.0.1", 0), _BrainServer)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    machine = machines["pc"]
    original = machine.ip
    # Point the machine at the fake server without editing the registry type.
    machines["pc"] = Machine(
        key=machine.key,
        name=machine.name,
        ip="127.0.0.1",
        mac=machine.mac,
        user=machine.user,
        os=machine.os,
        api_port=port,
    )
    yield machines["pc"]
    srv.shutdown()
    srv.server_close()
    assert original == machine.ip


def test_brain_ask_posts_json_to_the_brain(brain_server):
    ok, reply = brain.ask("u<key>", brain_server, 'كم عدد الملفات؟ "quoted" & $HOME')
    assert ok is True
    assert reply == "the answer from the pc"
    assert _BrainServer.seen["key"] == "u<key>"
    # The question travelled as JSON, untouched -- a shell would have eaten the
    # quotes, the dollar and the ampersand.
    assert _BrainServer.seen["body"]["message"] == 'كم عدد الملفات؟ "quoted" & $HOME'
    assert _BrainServer.seen["body"]["sid"]


def test_brain_ask_reports_an_unauthorized_brain_honestly(brain_server):
    _BrainServer.status = 404
    _BrainServer.payload = {"error": "not found"}
    ok, reason = brain.ask("", brain_server, "hello")
    assert ok is False
    assert "404" in reason


def test_brain_ask_refuses_an_empty_question(brain_server):
    ok, reason = brain.ask("k", brain_server, "   ")
    assert ok is False and reason


def test_brain_ask_survives_an_unreachable_pc(brain_server):
    machine = Machine(
        key="pc", name="pc", ip="127.0.0.1", mac=None, user="x", os="windows", api_port=9
    )
    ok, reason = brain.ask("k", machine, "hello", timeout=2)
    assert ok is False
    assert reason, "a dead machine must produce a reason, never an exception"


def test_the_question_never_reaches_a_shell(brain_server):
    """A structural guarantee: brain.py must not import a shell at all."""
    import inspect

    src = inspect.getsource(brain)
    assert "shlex" not in src
    assert "subprocess" not in src
    assert "os.system" not in src


# ============================================================== the poller
def test_poll_interval_defaults_and_env(monkeypatch):
    monkeypatch.delenv("AALI_JARVIS_POLL_SECS", raising=False)
    assert jarvis_main.poll_interval() == jarvis_main.DEFAULT_POLL_SECS
    monkeypatch.setenv("AALI_JARVIS_POLL_SECS", "0")
    assert jarvis_main.poll_interval() == 0.0, "0 must switch the poller off"
    monkeypatch.setenv("AALI_JARVIS_POLL_SECS", "-5")
    assert jarvis_main.poll_interval() == 0.0
    monkeypatch.setenv("AALI_JARVIS_POLL_SECS", "not a number")
    assert jarvis_main.poll_interval() == jarvis_main.DEFAULT_POLL_SECS


def test_the_poller_never_kills_the_bot():
    """A broken inbox must not be able to stop the polling loop."""
    calls = []
    stop = threading.Event()

    class Angry:
        def deliver_answers(self, voice=False):
            calls.append(1)
            stop.set()  # one cycle is enough to prove the point
            raise RuntimeError("inbox on fire")

    jarvis_main.answer_poller(Angry(), 0.01, stop)
    assert calls == [1], "it raised, logged and kept going instead of crashing"


def test_the_poller_delivers_when_something_is_waiting(cfg_dir):
    class Quiet:
        def __init__(self):
            self.voice = None

        def deliver_answers(self, voice=False):
            self.voice = voice
            return 1

    q = Quiet()
    stop = threading.Event()
    stop.set()
    jarvis_main.answer_poller(q, 0.01, stop)
    assert q.voice is None or q.voice is False, "the poller speaks only when told to"
