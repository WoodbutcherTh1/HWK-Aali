"""Tests for Piper TTS (Wave 1 #3, 2026-09-25).

The real Piper subprocess is mocked at the boundary — these tests pin the
CONTRACT: module behavior (voice discovery, caching, validation, error
paths) and endpoint permissions (tts gate, guest 404, empty 400, audio
serving). A live smoke against the real voice runs out-of-suite
(see docs/features/voice_output.md).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import tts  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture(autouse=True)
def _never_touch_real_voices(tmp_path, monkeypatch):
    """HARD GUARD (2026-09-25 lesson): an earlier buggy fake wrote a fake
    WAV ONTO the real 63MB voice model at D:/hwk-data/voices (endpoint
    tests ran without the voices fixture while the fake wrote to the
    wrong argv index). From now on EVERY test in this file — endpoints
    included — runs against a disposable voices dir; the real one is
    unreachable from the suite."""
    sandbox = tmp_path / "voices-sandbox"
    sandbox.mkdir()
    monkeypatch.setattr(tts, "VOICES_DIR", sandbox)
    monkeypatch.setattr(tts, "CACHE_DIR", sandbox / "cache")
    monkeypatch.setattr(tts, "LOG_FILE", tmp_path / "tts.log")
    fake_py = tmp_path / "fake-python.exe"
    fake_py.write_text("")
    monkeypatch.setattr(tts, "_CANDIDATE_PYTHONS", (fake_py,))
    return sandbox


@pytest.fixture()
def fake_voices(tmp_path, monkeypatch, _never_touch_real_voices):
    """Two fake voices (Arabic first) inside the sandbox."""
    voices_dir = tts.VOICES_DIR
    for name, lang in (
        ("ar_JO-kareem-medium", "ar_JO"),
        ("en_US-amy-medium", "en_US"),
    ):
        (voices_dir / f"{name}.onnx").write_bytes(b"fake-model")
        (voices_dir / f"{name}.onnx.json").write_text(
            '{"language": {"code": "%s"}}' % lang, encoding="utf-8")
    return voices_dir


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from file_agent import accounts as accounts_mod
    from file_agent import apikeys as apikeys_mod
    from file_agent import audit as audit_mod

    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts_mod._configure(tmp_path / "acc.jsonl")
    apikeys_mod._configure(tmp_path / "keys.jsonl")
    audit_mod._configure(tmp_path / "audit.jsonl")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _fake_wav(tmp_path, monkeypatch, ok=True, frames=2000):
    """Make tts.synthesize produce a real tiny wav via the child fake."""
    import struct
    import subprocess
    import wave

    def run(cmd, **kwargs):
        # cmd = [python, _child.py, model, out_tmp, length_scale] — the
        # child's OUTPUT is cmd[3] (argv[2] inside the child script).
        out = Path(cmd[3])
        out.parent.mkdir(parents=True, exist_ok=True)
        if ok:
            with wave.open(str(out), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(struct.pack("<" + "h" * frames,
                                           *([100] * frames)))
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, b"", b"")

    monkeypatch.setattr(tts.subprocess, "run", run)


def test_list_voices_arabic_first(fake_voices):
    voices = tts.list_voices()
    assert len(voices) == 2
    assert voices[0]["id"] == "ar_JO-kareem-medium"
    assert voices[0]["arabic"] is True


def test_default_voice_is_arabic(fake_voices):
    assert tts.default_voice() == "ar_JO-kareem-medium"


def test_available_true_with_setup(fake_voices):
    assert tts.available() is True


def test_available_false_without_python(tmp_path, monkeypatch, fake_voices):
    monkeypatch.setattr(
        tts, "_CANDIDATE_PYTHONS", (tmp_path / "missing.exe",))
    assert tts.available() is False


def test_synthesize_rejects_empty(fake_voices):
    assert tts.synthesize("   ")["ok"] is False


def test_synthesize_rejects_too_long(fake_voices, monkeypatch):
    monkeypatch.setattr(tts, "MAX_TEXT_CHARS", 10)
    assert tts.synthesize("x" * 11)["ok"] is False


def test_synthesize_unknown_voice(fake_voices):
    out = tts.synthesize("hi", voice_id="nope")
    assert out["ok"] is False
    assert "unknown voice" in out["error"]


def test_synthesize_ok_and_wav_valid(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    out = tts.synthesize("مرحبا")
    assert out["ok"] is True
    assert out["voice"] == "ar_JO-kareem-medium"
    assert Path(out["wav"]).exists()


def test_synthesize_cache_hit(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    first = tts.synthesize("same text")
    second = tts.synthesize("same text")
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["wav"] == first["wav"]


def test_synthesize_speed_variants_cached_separately(
        fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    a = tts.synthesize("text", speed=1.0)
    b = tts.synthesize("text", speed=1.5)
    assert a["wav"] != b["wav"]


def test_synthesize_speed_clamped(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    # 99 -> clamped to 2.0; no error, no NaN path
    out = tts.synthesize("text", speed=99)
    assert out["ok"] is True


def test_synthesize_invalid_wav_rejected(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch, ok=False)  # child writes nothing
    out = tts.synthesize("text")
    assert out["ok"] is False
    assert "failed" in out["error"]


# ————— endpoints —————

def test_endpoint_remote_unauthenticated_404(client):
    """Track B: remote + no credentials = guest -> uniform 404 — TTS never
    reveals itself to the unauthenticated."""
    r = client.post("/api/voice/synthesize", json={"text": "hi"},
                    headers={"X-Forwarded-For": "9.9.9.9"},
                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 404


def test_endpoint_remote_issued_key_user_can_tts(
        client, monkeypatch, tmp_path, fake_voices):
    """An AUTHENTICATED remote user keeps TTS: synthesis exposes no
    conversation data (unlike search) — text in, audio out."""
    from file_agent import apikeys as apikeys_mod
    _fake_wav(tmp_path, monkeypatch)
    _key_id, plaintext = apikeys_mod.issue_key("friend", "admin", "")
    r = client.post("/api/voice/synthesize", json={"text": "مرحبا"},
                    headers={"X-API-Key": plaintext,
                             "X-Forwarded-For": "9.9.9.9"},
                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 200


def test_endpoint_owner_can_synthesize(client, monkeypatch, tmp_path, fake_voices):
    _fake_wav(tmp_path, monkeypatch)
    r = client.post("/api/voice/synthesize", json={"text": "مرحبا يا عالم"},
                    headers={"X-API-Key": MASTER})
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("audio/wav")
    assert r.headers["X-Aali-Voice"] == "ar_JO-kareem-medium"


def test_endpoint_empty_text_400(client, monkeypatch, tmp_path):
    _fake_wav(tmp_path, monkeypatch)
    r = client.post("/api/voice/synthesize", json={"text": "  "},
                    headers={"X-API-Key": MASTER})
    assert r.status_code == 400


def test_endpoint_tts_failure_503(client, monkeypatch, tmp_path, fake_voices):
    _fake_wav(tmp_path, monkeypatch, ok=False)
    r = client.post("/api/voice/synthesize", json={"text": "hello"},
                    headers={"X-API-Key": MASTER})
    assert r.status_code == 503


def test_endpoint_voices_listing(client, monkeypatch, tmp_path, fake_voices):
    r = client.get("/api/voice/voices", headers={"X-API-Key": MASTER})
    assert r.status_code == 200
    body = r.get_json()
    assert body["available"] is True
    assert body["default"] == "ar_JO-kareem-medium"
    assert len(body["voices"]) == 2


def test_endpoint_requires_auth(client):
    r = client.post("/api/voice/synthesize", json={"text": "hi"})
    assert r.status_code == 404  # Track B: uniform 404, key mode
