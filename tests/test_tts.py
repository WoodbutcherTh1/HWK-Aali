"""Tests for Piper TTS (Wave 1 #3, full contract 3.1-3.11).

The real Piper subprocess is mocked at the boundary — these tests pin the
CONTRACT: module behavior (voice discovery w/ quality, caching, format
conversion, validation, error paths) and endpoint permissions/limits
(tts gate, uniform 404, 400/413/429/503, mp3, role-aware limits).

HARD GUARD (2026-09-25 lesson): an early buggy fake once overwrote the
REAL voice model at D:/hwk-models/piper — every test here runs against a
disposable sandbox; the real dir is unreachable from the suite.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import apikeys  # noqa: E402
from file_agent import tts  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Disposable voices/cache/log — the real D:/hwk-models/piper is
    unreachable from this suite (the model-overwrite lesson, pinned)."""
    root = tmp_path / "tts-sandbox"
    models = root / "piper"
    models.mkdir(parents=True)
    monkeypatch.setattr(tts, "MODELS_DIR", models)
    monkeypatch.setattr(tts, "CACHE_DIR", root / "cache")
    monkeypatch.setattr(tts, "LOG_FILE", root / "tts.log")
    fake_py = tmp_path / "fake-python.exe"
    fake_py.write_text("")
    monkeypatch.setattr(tts, "_CANDIDATE_PYTHONS", (fake_py,))
    return root


@pytest.fixture()
def fake_voices(sandbox):
    """Two Arabic voices (medium + low) + one English."""
    for name, lang, quality in (
        ("ar_JO-kareem-medium", "ar_JO", "medium"),
        ("ar_JO-kareem-low", "ar_JO", "low"),
        ("en_US-amy-medium", "en_US", "medium"),
    ):
        (sandbox / "piper" / f"{name}.onnx").write_bytes(b"fake-model")
        (sandbox / "piper" / f"{name}.onnx.json").write_text(
            '{"language": {"code": "%s"}, "audio": {"quality": "%s"}}'
            % (lang, quality), encoding="utf-8")
    return sandbox


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from file_agent import accounts as accounts_mod
    from file_agent import audit as audit_mod

    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts_mod._configure(tmp_path / "acc.jsonl")
    apikeys._configure(tmp_path / "keys.jsonl")
    audit_mod._configure(tmp_path / "audit.jsonl")
    app_module.app.config["TESTING"] = True
    app_module._TTS_RATE.clear()
    with app_module.app.test_client() as c:
        yield c


def _fake_wav(tmp_path, monkeypatch, ok=True, frames=4000):
    """Fake the piper child: writes a real tiny WAV to cmd[3]."""
    import struct
    import subprocess
    import wave

    def run(cmd, **kwargs):
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


def _h(key: str = MASTER) -> dict[str, str]:
    return {"X-API-Key": key}


# ————— module-level —————

def test_list_voices_arabic_first_with_quality(fake_voices):
    voices = tts.list_voices()
    assert [v["id"] for v in voices[:2]] == [
        "ar_JO-kareem-low", "ar_JO-kareem-medium"] or [
        v["arabic"] for v in voices[:2]] == [True, True]
    assert voices[-1]["arabic"] is False
    by_id = {v["id"]: v for v in voices}
    assert by_id["ar_JO-kareem-medium"]["quality"] == "medium"
    assert by_id["ar_JO-kareem-low"]["quality"] == "low"


def test_default_voice_is_arabic(fake_voices):
    assert tts.default_voice() == "ar_JO-kareem-low"  # alphabetical within AR


def test_available_true_with_setup(fake_voices):
    assert tts.available() is True


def test_available_false_without_python(sandbox, monkeypatch):
    monkeypatch.setattr(
        tts, "_CANDIDATE_PYTHONS", (sandbox / "missing.exe",))
    assert tts.available() is False


def test_synthesize_rejects_empty(fake_voices):
    assert tts.synthesize("   ")["ok"] is False


def test_synthesize_rejects_too_long(fake_voices):
    assert tts.synthesize("x" * (tts.MAX_TEXT_CHARS + 1))["ok"] is False


def test_synthesize_unknown_voice_404_shape(fake_voices):
    out = tts.synthesize("hi", voice_id="nope")
    assert out["ok"] is False
    assert out["error"].startswith("unknown voice")


def test_synthesize_ok_and_wav_valid(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    out = tts.synthesize("مرحبا")
    assert out["ok"] is True
    assert out["format"] == "wav"
    assert Path(out["audio"]).exists()


def test_synthesize_cache_hit(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    first = tts.synthesize("same text")
    second = tts.synthesize("same text")
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["audio"] == first["audio"]


def test_synthesize_speed_variants_cached_separately(
        fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    a = tts.synthesize("text", speed=1.0)
    b = tts.synthesize("text", speed=1.5)
    assert a["audio"] != b["audio"]


def test_synthesize_speed_clamped(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    assert tts.synthesize("text", speed=99)["ok"] is True
    assert tts.synthesize("text", speed=0.1)["ok"] is True


def test_synthesize_invalid_wav_rejected(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch, ok=False)
    out = tts.synthesize("text")
    assert out["ok"] is False
    assert "failed" in out["error"]


def test_synthesize_zero_chunk_wav_rejected_honestly(
        fake_voices, tmp_path, monkeypatch):
    """2026-09-25 zero-chunk incident: some texts (e.g. Arabic the espeak-ng
    lexicon mishandles) phonemize to NOTHING — piper yields zero chunks. The
    patched child then exits rc=0 with a VALID but 0-frame WAV header. The
    parent must reject that honestly (never cache, never serve silence,
    never crash with wave.Error '# channels not specified')."""
    import struct
    import subprocess
    import wave

    def run(cmd, **kwargs):
        out = Path(cmd[3])
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wf:  # valid header, ZERO frames
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(struct.pack("<" + "h" * 0))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")  # rc=0!

    monkeypatch.setattr(tts.subprocess, "run", run)
    out = tts.synthesize("تجربة الصوت الجديد")
    # Honest rejection (44-byte header is caught by the size gate, larger
    # garbage by the wave-frame check) — never cached, never served.
    assert out["ok"] is False
    assert out["error"]
    assert not list((fake_voices / "cache").glob("*.wav"))
    assert not list((fake_voices / "cache").glob("*.mp3"))


def test_child_script_pins_zero_chunk_fix():
    """Source tripwire: the child pre-sets the WAV format itself and disables
    piper's per-chunk set_wav_format, so a no-chunk run closes cleanly as
    0 frames instead of blowing up in the with-block close()."""
    assert "set_wav_format=False" in tts._CHILD_SCRIPT
    assert "setnchannels(1)" in tts._CHILD_SCRIPT
    assert "setsampwidth(2)" in tts._CHILD_SCRIPT
    assert "setframerate" in tts._CHILD_SCRIPT
    assert "finally" in tts._CHILD_SCRIPT


def test_synthesize_arabic_and_mixed(fake_voices, tmp_path, monkeypatch):
    _fake_wav(tmp_path, monkeypatch)
    assert tts.synthesize("السلام عليكم")["ok"] is True
    assert tts.synthesize("استعمل python في الكود")["ok"] is True


def test_mp3_honest_fallback_without_ffmpeg(fake_voices, tmp_path,
                                             monkeypatch):
    """format=mp3 + no ffmpeg → WAV served with an honest note."""
    _fake_wav(tmp_path, monkeypatch)
    monkeypatch.setattr(tts, "_ffmpeg", lambda: None)
    out = tts.synthesize("text", fmt="mp3")
    assert out["ok"] is True
    assert out["format"] == "wav"
    assert out.get("note")


def test_mp3_success_when_ffmpeg_available(fake_voices, tmp_path,
                                            monkeypatch):
    """format=mp3 + ffmpeg → real .mp3 served (ffmpeg faked: copies the
    -i wav to the output path, like the real thing would)."""
    import shutil
    import subprocess

    _fake_wav(tmp_path, monkeypatch)
    fake_ff = tmp_path / "ffmpeg.exe"
    fake_ff.write_text("")
    monkeypatch.setattr(tts, "_ffmpeg", lambda: fake_ff)

    real_run = tts.subprocess.run

    def run(cmd, **kwargs):
        if Path(cmd[0]) == fake_ff:
            src = Path(cmd[cmd.index("-i") + 1])
            dst = Path(cmd[-1])
            shutil.copyfile(src, dst)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(tts.subprocess, "run", run)
    out = tts.synthesize("text", fmt="mp3")
    assert out["ok"] is True
    assert out["format"] == "mp3"
    assert Path(out["audio"]).suffix == ".mp3"


# ————— endpoint-level —————

def test_endpoint_remote_unauthenticated_404(client):
    """Track B: remote + no credentials = guest -> uniform 404."""
    r = client.post("/api/voice/synthesize", json={"text": "hi"},
                    headers={"X-Forwarded-For": "9.9.9.9"},
                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 404


def test_endpoint_owner_can_synthesize(client, monkeypatch, tmp_path,
                                       fake_voices):
    _fake_wav(tmp_path, monkeypatch)
    r = client.post("/api/voice/synthesize", json={"text": "مرحبا يا عالم"},
                    headers=_h())
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("audio/wav")
    assert r.headers["X-Aali-Voice"] == "ar_JO-kareem-low"
    assert r.headers["X-Aali-Format"] == "wav"


def test_endpoint_empty_text_400(client, fake_voices):
    r = client.post("/api/voice/synthesize", json={"text": "  "}, headers=_h())
    assert r.status_code == 400


def test_endpoint_too_long_413(client, fake_voices, monkeypatch):
    monkeypatch.setattr(tts, "MAX_TEXT_CHARS", 2000)
    r = client.post("/api/voice/synthesize",
                    json={"text": "x" * 2001}, headers=_h())
    assert r.status_code == 413


def test_endpoint_unknown_voice_404(client, fake_voices, monkeypatch,
                                    tmp_path):
    _fake_wav(tmp_path, monkeypatch)
    r = client.post("/api/voice/synthesize",
                    json={"text": "hi", "voice": "nope"}, headers=_h())
    assert r.status_code == 404


def test_endpoint_tts_failure_503(client, monkeypatch, tmp_path,
                                  fake_voices):
    _fake_wav(tmp_path, monkeypatch, ok=False)
    r = client.post("/api/voice/synthesize", json={"text": "hello"},
                    headers=_h())
    assert r.status_code == 503


def test_endpoint_rate_limit_429(client, fake_voices, monkeypatch,
                                 tmp_path):
    _fake_wav(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "_TTS_RATE_MAX", 2)
    assert client.post("/api/voice/synthesize", json={"text": "a"},
                       headers=_h()).status_code == 200
    assert client.post("/api/voice/synthesize", json={"text": "b"},
                       headers=_h()).status_code == 200
    assert client.post("/api/voice/synthesize", json={"text": "c"},
                       headers=_h()).status_code == 429


def test_endpoint_role_aware_guest_limit_contract(client, fake_voices):
    """Guests never reach TTS (uniform 404); the 500-char guest limit is
    encoded for the day a guest-allowed variant ships — the constant must
    exist and be smaller than the user cap."""
    assert 0 < tts.GUEST_TEXT_CHARS < tts.MAX_TEXT_CHARS


def test_endpoint_voice_list(client, fake_voices):
    r = client.get("/api/voice/list", headers=_h())
    assert r.status_code == 200
    body = r.get_json()
    assert body["available"] is True
    assert body["default"] == "ar_JO-kareem-low"
    assert len(body["voices"]) == 3
    assert {"id", "name", "language", "quality"} <= set(body["voices"][0])


def test_endpoint_voices_alias(client, fake_voices):
    r = client.get("/api/voice/voices", headers=_h())
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_endpoint_authenticated_remote_user_can_tts(
        client, monkeypatch, tmp_path, fake_voices):
    _fake_wav(tmp_path, monkeypatch)
    _key_id, plaintext = apikeys.issue_key("friend", "admin", "")
    r = client.post("/api/voice/synthesize", json={"text": "مرحبا"},
                    headers={"X-API-Key": plaintext,
                             "X-Forwarded-For": "9.9.9.9"},
                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 200


def test_endpoint_requires_auth(client):
    r = client.post("/api/voice/synthesize", json={"text": "hi"})
    assert r.status_code == 404  # Track B: uniform 404, key mode
