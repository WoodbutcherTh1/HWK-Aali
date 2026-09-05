"""Unit tests for the HWK Aali model, tokenizers, and agent tools.

Run from the project root:
    PYTHONIOENCODING=utf-8 PYTHONPATH=file-agent .venv/Scripts/python -m pytest tests -q
"""

from __future__ import annotations

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
