"""Tests for the Jarvis bot.

The centrepiece is :class:`MockTelegram`, a real local HTTP server that speaks
the Bot API, so the whole inbound -> route -> reply path runs end to end with
no token and no network.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from jarvis import groq, log, power, tts, wol
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

    def fake_synth(text, out_path=None, runner=None, voice=None):
        return made

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    monkeypatch.setattr(tts, "available", lambda: True)
    jarvis.handle_update(text_update(OWNER, "/help"), voice=True)
    assert mock_tg.sent_voice, "expected a voice note"


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