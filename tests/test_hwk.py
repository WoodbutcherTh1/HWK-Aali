"""Unit tests for the HWK Aali model, tokenizers, and agent tools.

Run from the project root:
    PYTHONIOENCODING=utf-8 PYTHONPATH=file-agent .venv/Scripts/python -m pytest tests -q
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import pytest
import torch

PROJECT_PACKAGE = Path(__file__).resolve().parent.parent / "file-agent"

BPE_MODEL = Path("D:/hwk-data/tokenizer/hwk_spm.model")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _tiny_model(**overrides):
    from hwk_model import ModelConfig, TinyCausalLM

    config = ModelConfig(
        vocab_size=259, context_size=128, d_model=64, n_heads=4, n_layers=3, dropout=0.0
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return TinyCausalLM(config)


def test_model_init_loss_matches_random_guessing():
    torch.manual_seed(0)
    model = _tiny_model()
    inputs = torch.randint(0, 259, (2, 64))
    targets = torch.randint(0, 259, (2, 64))
    _, loss = model(inputs, targets)
    assert abs(float(loss) - math.log(259)) < 0.15


def test_model_learns_from_synthetic_data():
    torch.manual_seed(1)
    model = _tiny_model()
    inputs = torch.randint(0, 259, (2, 64))
    targets = torch.randint(0, 259, (2, 64))
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    _, first = model(inputs, targets)
    for _ in range(30):
        optimizer.zero_grad()
        _, loss = model(inputs, targets)
        loss.backward()
        optimizer.step()
    _, last = model(inputs, targets)
    assert float(last) < float(first)


def test_model_forward_output_shape():
    model = _tiny_model()
    logits = model(torch.zeros(3, 32, dtype=torch.long))
    assert tuple(logits.shape) == (3, 32, 259)


def test_model_rejects_sequence_longer_than_context():
    model = _tiny_model()
    with pytest.raises(ValueError):
        model(torch.zeros(1, 129, dtype=torch.long))


def test_model_rope_extrapolates_at_inference():
    """RoPE lets generation run at up to the trained context; shorter prompts
    at arbitrary lengths must all work without position-table lookups."""
    model = _tiny_model(context_size=128)
    for length in (1, 7, 64, 127):
        logits = model(torch.zeros(1, length, dtype=torch.long))
        assert tuple(logits.shape) == (1, length, 259)


def test_checkpoint_roundtrip(tmp_path):
    from hwk_model import save_checkpoint

    model = _tiny_model()
    save_checkpoint(model, tmp_path / "ckpt.pt", step=7)
    payload = torch.load(tmp_path / "ckpt.pt", map_location="cpu", weights_only=False)
    assert payload["step"] == 7
    assert payload["config"]["n_layers"] == 3


# ---------------------------------------------------------------------------
# Tokenizers
# ---------------------------------------------------------------------------

def test_byte_tokenizer_roundtrip():
    from hwk_model import ByteTokenizer

    tokenizer = ByteTokenizer()
    sample = "Hello world! مرحبا بالعالم 123 \n"
    assert tokenizer.decode(tokenizer.encode(sample)) == sample


@pytest.mark.skipif(not BPE_MODEL.exists(), reason="BPE model not trained yet")
def test_bpe_tokenizer_roundtrip_and_vocab():
    from hwk_model import load_bpe

    tokenizer = load_bpe(BPE_MODEL)
    assert tokenizer.vocab_size == 32000
    for sample in ("def add(a, b): return a + b", "مرحبا بالعالم، كيف حالك؟"):
        assert tokenizer.decode(tokenizer.encode(sample)) == sample


# ---------------------------------------------------------------------------
# Agent file tools
# ---------------------------------------------------------------------------

def _tool(name, arguments, root, monkeypatch=None):
    from file_agent.file_tools import execute_tool

    return execute_tool(name, arguments, root)


def test_file_write_read(tmp_path):
    result = _tool("write_file", {"path": "notes/today.txt", "content": "مرحبا"}, tmp_path)
    assert result["ok"] and result["result"]["created"]
    result = _tool("read_file", {"path": "notes/today.txt"}, tmp_path)
    assert result["result"]["content"] == "مرحبا"


def test_overwrite_requires_flag(tmp_path):
    _tool("write_file", {"path": "a.txt", "content": "one"}, tmp_path)
    result = _tool("write_file", {"path": "a.txt", "content": "two"}, tmp_path)
    assert not result["ok"]
    result = _tool("write_file", {"path": "a.txt", "content": "two", "overwrite": True}, tmp_path)
    assert result["ok"]


def test_path_escape_is_blocked(tmp_path):
    result = _tool("read_file", {"path": "../outside.txt"}, tmp_path)
    assert not result["ok"]


def test_search_files(tmp_path):
    (tmp_path / "app.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    result = _tool("search_files", {"pattern": "print"}, tmp_path)
    assert result["ok"]
    matches = result["result"]["matches"]
    assert len(matches) == 1 and matches[0]["path"] == "app.py" and matches[0]["line"] == 2


def test_read_image_ocr_extracts_text(tmp_path):
    """read_image OCRs a real image; must succeed (text or honest empty note)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pytest.skip("Pillow not installed")
    image_path = tmp_path / "sample.png"
    img = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    draw.text((30, 30), "HWK OCR test line one", fill="black", font=font)
    draw.text((30, 100), "HWK OCR test line two", fill="black", font=font)
    img.save(image_path)

    result = _tool("read_image", {"path": "sample.png"}, tmp_path)
    assert result["ok"], result
    body = result["result"]
    assert body["text"] != "" or "No readable text" in body.get("note", "")


def test_tool_definitions_include_read_image():
    from file_agent.file_tools import get_tool_definitions

    names = {d["function"]["name"] for d in get_tool_definitions()}
    assert {"read_image", "read_document", "analyze_video"} <= names


def test_read_document_docx_and_blocked_types(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("سطر تجريبي بالعربية")
    document.add_paragraph("English line too")
    document.save(str(tmp_path / "doc.docx"))

    result = _tool("read_document", {"path": "doc.docx"}, tmp_path)
    assert result["ok"]
    text = result["result"]["text"]
    assert "سطر تجريبي" in text and "English line" in text

    (tmp_path / "bad.exe").write_bytes(b"MZ")
    blocked = _tool("read_document", {"path": "bad.exe"}, tmp_path)
    assert not blocked["ok"]


def test_run_command_allowed_and_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("HWK_ALLOW_COMMANDS", "1")
    result = _tool("run_command", {"command": 'python -c "print(6*7)"', "timeout_seconds": 60}, tmp_path)
    assert result["ok"] and result["result"]["output"].strip() == "42"
    blocked = _tool("run_command", {"command": "format c:"}, tmp_path)
    assert not blocked["ok"]
    git_blocked = _tool("run_command", {"command": "git push origin main"}, tmp_path)
    assert not git_blocked["ok"]


def test_run_command_disabled_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("HWK_ALLOW_COMMANDS", "0")
    result = _tool("run_command", {"command": 'python -c "pass"'}, tmp_path)
    assert not result["ok"]


def test_run_command_blocks_env_exfiltration(tmp_path, monkeypatch):
    """Security: commands that dump environment variables/credentials must
    never execute (nx/npm 2025 lesson - this is how keys leave machines)."""
    monkeypatch.setenv("HWK_ALLOW_COMMANDS", "1")
    for command in (
        'python -c "import os; print(os.environ)"',
        'node -e "console.log(process.env)"',
        "echo $SECRET_TOKEN",
        'python -c "print(open(\'.env\').read())"',
    ):
        result = _tool("run_command", {"command": command}, tmp_path)
        assert not result["ok"], command


def test_run_command_output_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HWK_ALLOW_COMMANDS", "1")
    result = _tool("run_command", {
        "command": 'python -c "print(\'key = sk-abc123DEF456ghi789\')"',
    }, tmp_path)
    assert result["ok"]
    assert "sk-abc123DEF456ghi789" not in result["result"]["output"]


# ---------------------------------------------------------------------------
# Long-term memory (file_agent/memory.py)
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_dir(tmp_path, monkeypatch):
    """Isolated memory store per test (env var wins over ~/.aali)."""
    monkeypatch.setenv("AALI_MEMORY_DIR", str(tmp_path / "aali-mem"))
    return tmp_path / "aali-mem"


def test_memory_remember_recall_roundtrip(mem_dir):
    from file_agent import memory

    memory.remember("لا تحذف مجلد المشروع أبدًا", kind="instruction", topic="deletion")
    memory.remember("always answer in Arabic", kind="preference")
    result = memory.recall(query="حذف مجلد")
    assert result["ok"] and result["result"]["count"] == 1
    entry = result["result"]["entries"][0]
    assert entry["kind"] == "instruction" and "لا تحذف" in entry["text"]
    assert entry["topic"] == "deletion" and entry["created"]


def test_memory_reaffirm_bumps_confirmation(mem_dir):
    from file_agent import memory

    first = memory.remember("always use bf16", kind="preference")
    second = memory.remember("always use bf16", kind="preference")
    assert second["result"]["action"] == "reaffirmed"
    assert second["result"]["entry"]["confirmations"] == 2
    assert second["result"]["entry"]["id"] == first["result"]["entry"]["id"]


def test_memory_contradiction_surfaced(mem_dir):
    from file_agent import memory

    memory.remember("never push to GitHub", kind="decision", topic="git")
    second = memory.remember("push to GitHub daily", kind="decision", topic="git")
    contradiction = second["result"]["contradiction"]
    assert contradiction is not None
    assert "never push" in contradiction["text"]
    # Both remain stored; newest-first rendering puts the new one on top.
    rendered = memory.render_block()
    assert "push to GitHub daily" in rendered.splitlines()[4]


def test_memory_rejects_agent_beliefs_and_bad_kind(mem_dir):
    from file_agent import memory

    with pytest.raises(memory.MemoryError):
        memory.remember("the user's password is horse", source="agent")
    with pytest.raises(memory.MemoryError):
        memory.remember("anything", kind="rumor")
    with pytest.raises(memory.MemoryError):
        memory.remember("   ")


def test_memory_redacts_secrets(mem_dir):
    from file_agent import memory

    saved = memory.remember("my api_key = sk-abc123DEF456ghi789 keep it safe")
    text = saved["result"]["entry"]["text"]
    assert "sk-abc123DEF456ghi789" not in text
    assert memory._SECRET_MARKER in text
    # The memory block sent to providers must never contain the raw secret.
    assert "sk-abc123DEF456ghi789" not in memory.render_block()


def test_memory_forget_requires_criteria_and_gates(mem_dir):
    from file_agent import memory

    memory.remember("old preference", kind="preference")
    with pytest.raises(memory.MemoryError):
        memory.forget()  # no criteria -> refuse
    result = memory.forget(kind="preference")
    assert result["result"]["count"] == 1
    assert memory.recall()["result"]["total"] == 0


def test_memory_empty_recall_is_honest(mem_dir):
    from file_agent import memory

    result = memory.recall(query="anything at all")
    assert result["ok"] and result["result"]["count"] == 0


def test_record_turn_auto_captures_directives_and_logs_all(mem_dir):
    from file_agent import memory

    memory.record_turn("user", "من الآن لا تستخدم fp16 في التدريب أبدًا")
    memory.record_turn("assistant", "تم يا صديقي")
    log = (mem_dir / memory.CONVERSATION_LOG_NAME).read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 2  # raw turns kept for mentor-SFT
    saved = memory.recall(kind="decision")
    assert saved["result"]["count"] == 1  # directive auto-captured
    # Ordinary chat is NOT auto-captured.
    memory.record_turn("user", "مرحبا كيف حالك اليوم")
    assert memory.recall(kind="decision")["result"]["count"] == 1


def test_memory_tool_via_execute_tool(mem_dir, tmp_path):
    from file_agent.file_tools import execute_tool

    ok = execute_tool("memory", {"action": "save", "text": "prefer Arabic replies",
                                 "kind": "preference"}, tmp_path)
    assert ok["ok"]
    found = execute_tool("memory", {"action": "recall", "query": "Arabic"}, tmp_path)
    assert found["ok"] and found["result"]["count"] == 1
    bad_kind = execute_tool("memory", {"action": "save", "text": "x", "kind": "nope"}, tmp_path)
    assert not bad_kind["ok"]


def test_agent_loop_records_conversation_turns(tmp_path, monkeypatch, mem_dir):
    from agent_loop import agent_loop
    from file_agent import memory

    monkeypatch.delenv("LOCAL_MODEL_PATH", raising=False)
    monkeypatch.setenv("AALI_OLLAMA", "0")
    agent_loop("أنشئ ملف mem_test.txt واكتب بداخله تم", tmp_path, mode="local", print_final=False)
    log = (mem_dir / memory.CONVERSATION_LOG_NAME).read_text(encoding="utf-8").strip().splitlines()
    assert len(log) >= 2
    assert any("mem_test.txt" in line for line in log)


def test_memory_block_included_in_scratch_transcript(tmp_path, monkeypatch, mem_dir):
    from file_agent import memory
    import agent_loop

    memory.remember("owner name is Hmam", kind="fact")
    monkeypatch.setenv("AALI_OLLAMA", "0")  # force the scratch-model path
    fake_ckpt = tmp_path / "final.pt"
    fake_ckpt.write_bytes(b"x")  # exists-check passes; loader is patched below
    monkeypatch.setenv("LOCAL_MODEL_PATH", str(fake_ckpt))

    captured = {}

    def fake_load_checkpoint(path, device=None):
        return object(), {"tokenizer": {}}

    def fake_generate(model, tokenizer, prompt, **kwargs):
        captured["prompt"] = prompt
        return '{"tool": "final", "content": "ok"}'

    monkeypatch.setattr("hwk_model.load_checkpoint", fake_load_checkpoint)
    monkeypatch.setattr("hwk_model.generation.generate_text", fake_generate)
    reply = agent_loop.agent_loop("hi", tmp_path, mode="local", print_final=False)
    assert reply == "ok"
    assert "owner name is Hmam" in captured["prompt"]
    assert "System:" in captured["prompt"] and "Available tools:" in captured["prompt"]


# ---------------------------------------------------------------------------
# Suggestions (the "what next?" chips)
# ---------------------------------------------------------------------------

def test_suggestions_match_language_and_cap():
    from file_agent.suggestions import suggest

    arabic = suggest("تم إنشاء الملف بنجاح في مجلد العمل", "أنشئ لي ملف")
    assert 1 <= len(arabic) <= 3
    assert all(any("\u0600" <= ch <= "\u06FF" for ch in s) for s in arabic)
    english = suggest("Created the file successfully", "create a file")
    assert all(not any("\u0600" <= ch <= "\u06FF" for ch in s) for s in english)


def test_suggestions_never_suggest_destructive():
    from file_agent.suggestions import suggest

    for reply in ("deleted the folder", "installed the package", "killed process 42"):
        for text in suggest(reply, reply):
            lowered = text.lower()
            assert not any(word in lowered for word in ("delete", "احذف", "uninstall", "kill"))


# ---------------------------------------------------------------------------
# Emoji intelligence
# ---------------------------------------------------------------------------

def test_emoji_analyze_reads_emotion_and_language():
    from file_agent import emoji

    ar = emoji.analyze("نجحت بالامتحان 🎉🎉")
    assert ar["emotion"] == "celebration" and ar["user_is_arabic"]
    assert "مبروك" in ar["reaction"]
    en = emoji.analyze("my files got deleted 😭")
    assert en["emotion"] == "sadness"
    none = emoji.analyze("plain text no emoji")
    assert none["emotion"] == "none" and none["reaction"] == ""


def test_emoji_decorate_rules():
    from file_agent import emoji

    # Success earns exactly one fitting emoji.
    done = emoji.decorate("تم إنشاء الملف بنجاح", "شكرا 😄")
    assert len(emoji._EMOJI.findall(done)) == 1
    # Refusals and errors are never decorated.
    assert emoji.decorate("I can't print environment variables", "please 🎉") == \
        "I can't print environment variables"
    assert emoji.decorate("تعذر الحذف: المجلد غير موجود", "احذفه 🙏") == \
        "تعذر الحذف: المجلد غير موجود"
    # Never piles on when a reply already has one.
    assert emoji.decorate("تم ✅", "أحسنت 😄") == "تم ✅"
    # Serious topics stay sober.
    assert emoji.decorate("Never store the password in the file", "كلمة السر 🔑") == \
        "Never store the password in the file"


def test_generate_emoji_tool(tmp_path):
    from file_agent.file_tools import execute_tool

    result = execute_tool("generate_emoji", {
        "prompt": "a cool blue emoji", "path": "emoji/cool.png",
        "emotion": "cool", "palette": "blue",
    }, tmp_path)
    assert result["ok"] and result["result"]["emotion"] == "cool"
    assert (tmp_path / "emoji" / "cool.png").exists()


def test_autocorrect_basic_and_protection():
    from file_agent import autocorrect

    fixed = autocorrect.correct("hai dode can u help me")
    assert fixed.corrected == "hi dude can you help me"
    assert fixed.n_changes == 3
    hint = autocorrect.hint_phrase(fixed, arabic=False)
    assert "hi dude" in hint
    # Paths/code survive untouched.
    path_case = autocorrect.correct("read D:/hwk-data/training.log and `x = os.environ`")
    assert "D:/hwk-data/training.log" in path_case.corrected
    assert "os.environ" in path_case.corrected
    # Arabic normalization.
    ar = autocorrect.correct("لاكن انا سعيد")
    assert ar.corrected.startswith("لكن أنا")
    # Already-clean text changes nothing.
    clean = autocorrect.correct("please open the training folder")
    assert clean.n_changes == 0


# ---------------------------------------------------------------------------
# Memory: edit/get + semantic recall
# ---------------------------------------------------------------------------

def test_memory_edit_and_get(mem_dir):
    from file_agent import memory

    entry = memory.remember("likes tea", kind="preference")["result"]["entry"]
    fetched = memory.get(entry["id"])["result"]["entry"]
    assert fetched["text"] == "likes tea"
    edited = memory.edit(entry["id"], text="likes coffee", topic="drinks")["result"]
    assert edited["entry"]["text"] == "likes coffee" and edited["entry"]["topic"] == "drinks"
    with pytest.raises(memory.MemoryError):
        memory.get("no-such-id")
    with pytest.raises(memory.MemoryError):
        memory.edit("no-such-id", text="x")


def test_memory_edit_merges_duplicate(mem_dir):
    from file_agent import memory

    first = memory.remember("likes tea", kind="preference")["result"]["entry"]
    second = memory.remember("prefers tea in the morning", kind="preference")["result"]["entry"]
    result = memory.edit(second["id"], text="Likes Tea")["result"]
    assert result["action"] == "merged"
    assert result["merged_into"] == first["id"]
    assert memory.recall()["result"]["total"] == 1


def test_recall_keyword_mode_without_embedder(mem_dir, monkeypatch):
    from file_agent import memory

    monkeypatch.setenv("AALI_EMBEDDINGS", "0")
    memory.remember("never push to GitHub without asking", kind="decision", topic="git")
    result = memory.recall(query="deployment rules")
    assert result["result"]["mode"] == "keyword"
    assert result["result"]["count"] == 0  # no shared words -> honest empty


def test_recall_hybrid_mode_finds_related_entry(mem_dir, monkeypatch):
    """Semantic: 'deployment rules' must find the GitHub push rule even with
    zero shared keywords (fake embedder: same vector for related topics)."""
    from file_agent import memory

    fake_vectors = {"deploy": [1.0, 0.0], "unrelated": [0.0, 1.0]}

    def fake_embed(texts):
        out = []
        for text in texts:
            lowered = text.lower()
            if any(w in lowered for w in ("deploy", "push", "github", "git")):
                out.append(list(fake_vectors["deploy"]))
            else:
                out.append(list(fake_vectors["unrelated"]))
        return out

    monkeypatch.setattr(memory, "_ollama_embed", fake_embed)
    memory.remember("never push to GitHub without asking", kind="decision", topic="git")
    result = memory.recall(query="deployment rules")
    assert result["result"]["mode"] == "hybrid"
    assert result["result"]["count"] == 1
    assert "GitHub" in result["result"]["entries"][0]["text"]
    # Sidecar cached the vector for the entry.
    sidecar = memory._load_sidecar()
    assert memory._EMBED_MODEL in str(sidecar)


# ---------------------------------------------------------------------------
# SFT v2 builder: exam-leak gate
# ---------------------------------------------------------------------------

def test_sft_v2_exam_leak_gate(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_aali_sft_v2 as builder

    exam_case = {"id": "t", "turns": [["user", "SECRET-EXAM-PROMPT-123"], ["assistant", "x"]]}
    exam_path = tmp_path / "exam.jsonl"
    exam_path.write_text(json.dumps(exam_case), encoding="utf-8")
    hashes = builder.load_exam_prompts(exam_path)
    assert builder._hash_pair("SECRET-EXAM-PROMPT-123", "") in hashes
    assert builder._hash_pair("harmless prompt", "") not in hashes


def test_agent_workspace_and_instructions(tmp_path, monkeypatch):
    from agent_loop import agent_loop

    monkeypatch.delenv("LOCAL_MODEL_PATH", raising=False)
    # Pin the deterministic path so the test never depends on Ollama running.
    monkeypatch.setenv("AALI_OLLAMA", "0")
    response = agent_loop(
        "أنشئ ملف build_test.txt واكتب بداخله نجاح",
        tmp_path,
        mode="local",
        print_final=False,
    )
    assert (tmp_path / "build_test.txt").exists()
    assert "نجاح" in response or "نجاح" in (tmp_path / "build_test.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Live-event bus + streaming server (desktop app)
# ---------------------------------------------------------------------------

def test_agent_log_bus_delivers_events_to_subscriber():
    import agent_log

    request_id = "test-bus-123"
    queue = agent_log.subscribe(request_id)
    try:
        agent_log.log_event(request_id, "tool_requested", tool="write_file", arguments={"path": "a.txt"})
        event = queue.get(timeout=2)
        assert event["event"] == "tool_requested"
        assert event["tool"] == "write_file"
        # other requests never leak into this queue
        agent_log.log_event("other-request", "tool_requested", tool="read_file")
        assert queue.empty()
    finally:
        agent_log.unsubscribe(request_id)
    agent_log.log_event(request_id, "tool_result")
    assert queue.empty()  # unsubscribed → no delivery


def test_agent_loop_request_id_passthrough(tmp_path, monkeypatch):
    import agent_log
    from agent_loop import agent_loop

    monkeypatch.setenv("AALI_OLLAMA", "0")
    monkeypatch.delenv("LOCAL_MODEL_PATH", raising=False)
    rid = "fixed-rid-42"
    queue = agent_log.subscribe(rid)
    try:
        agent_loop("اكتب ملف rid_test.txt", tmp_path, mode="local", print_final=False, request_id=rid)
        first = queue.get(timeout=5)
        assert first["request_id"] == rid
        assert first["event"] == "request_started"
    finally:
        agent_log.unsubscribe(rid)


def test_sessions_api_list_get_delete(tmp_path, monkeypatch):
    """The sidebar endpoints over an isolated session store."""
    import importlib

    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setattr("app.SESSIONS_FILE", tmp_path / "sessions_test.jsonl")
    import app as app_mod

    importlib.reload(app_mod)
    client = app_mod.app.test_client()
    app_mod._sessions.clear()
    app_mod._sessions_loaded = True
    # a session is created by /api/ask… but that would call the brain; seed directly
    rec = app_mod._get_session("sess-abc")
    app_mod._append_turn(rec, "user", "مرحبا يا آلي")
    app_mod._append_turn(rec, "assistant", "أهلا بك")

    rows = client.get("/api/sessions").get_json()
    assert rows["ok"] and len(rows["sessions"]) == 1
    assert rows["sessions"][0]["sid"] == "sess-abc"
    assert "مرحبا" in rows["sessions"][0]["title"]

    got = client.get("/api/session/sess-abc").get_json()
    assert got["ok"] and len(got["turns"]) == 2
    assert client.get("/api/session/missing").status_code == 404

    assert client.delete("/api/session/sess-abc").get_json()["ok"]
    assert client.get("/api/session/sess-abc").status_code == 404
    app_mod._sessions.clear()


def test_api_key_gate_blocks_unauthorized(monkeypatch, tmp_path):
    """Multi-user mode: no/wrong X-API-Key → 401 on /api/*."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    import importlib

    import app as app_mod

    monkeypatch.setattr(app_mod, "API_KEY", "secret-key-1")
    client = app_mod.app.test_client()
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/health", headers={"X-API-Key": "secret-key-1"}).status_code == 200
    # non-API routes (the UI) stay open
    assert client.get("/ui/").status_code in (200, 404)
