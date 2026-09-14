"""HuggingFace uploader for Aali checkpoints — STEP 10.3.

Converts the repo's native checkpoint format (``{config, model, step,
tokenizer}`` from hwk_model.save_checkpoint, final.pt / checkpoint.pt) to
safetensors + standard metadata files, then uploads to the HuggingFace Hub.

Safety rails:
- AALI_UPLOAD_ENABLED=1 is REQUIRED for any network upload (the owner
  approves every publication); without it the script exits before any
  traffic. Conversion is local and always available.
- Idempotent: files already present in the repo with the same hash are
  skipped. ``--force`` re-uploads everything.
- The huggingface_hub import is lazy and only required by ``--upload``.

Examples:
    python scripts/hf_uploader.py --model D:/hwk-models/aali-sft-4k --repo HWK/aali-110m --private
    python scripts/hf_uploader.py --model tuned/checkpoint-3873 --convert-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

SUPPORTED_CHECKPOINTS = ("final.pt", "checkpoint.pt")
SUPPORTED_ADAPTERS = ("adapter_model.safetensors", "adapter_config.json")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_checkpoint_payload(path: Path) -> dict[str, Any]:
    """Load a repo-format .pt checkpoint (torch, weights_only=False)."""
    try:
        import torch
    except ImportError as exc:
        _die("torch is required for conversion (training venv has it)")
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict) or "model" not in payload \
            or "config" not in payload:
        _die(f"{path.name} is not a repo-format checkpoint "
             "(missing 'model'/'config' keys)")
    return payload


def convert_full_model(model_dir: Path, out_dir: Path) -> list[Path]:
    """Convert final.pt/checkpoint.pt → safetensors + config files."""
    try:
        from safetensors.torch import save_file
    except ImportError as exc:
        _die("safetensors is required for conversion")
    source = next((model_dir / n for n in SUPPORTED_CHECKPOINTS
                   if (model_dir / n).exists()), None)
    if source is None:
        _die(f"no {'/'.join(SUPPORTED_CHECKPOINTS)} found in {model_dir}")
    print(f"[convert] {source}")
    payload = load_checkpoint_payload(source)
    state = {k: v.contiguous() for k, v in payload["model"].items()}
    out_dir.mkdir(parents=True, exist_ok=True)
    out_files: list[Path] = []
    st_path = out_dir / "model.safetensors"
    save_file(state, str(st_path), metadata={"format": "pt"})
    out_files.append(st_path)
    # config.json straight from the checkpoint's own config dict
    cfg = payload.get("config")
    (out_dir / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    out_files.append(out_dir / "config.json")
    (out_dir / "generation_config.json").write_text(json.dumps({
        "eos_token_id": payload.get("tokenizer", {}).get("eos_id"),
        "pad_token_id": payload.get("tokenizer", {}).get("pad_id"),
        "bos_token_id": payload.get("tokenizer", {}).get("bos_id"),
    }, indent=2), encoding="utf-8")
    out_files.append(out_dir / "generation_config.json")
    # tokenizer sidecar when the checkpoint names one
    tok_meta = payload.get("tokenizer") or {}
    model_path = tok_meta.get("model_path")
    if model_path and Path(model_path).exists():
        target = out_dir / Path(model_path).name
        target.write_bytes(Path(model_path).read_bytes())
        out_files.append(target)
        print(f"[convert] tokenizer sidecar: {target.name}")
    # model card generated from the registry metadata + checkpoint config
    from scripts import generate_model_card as gmc  # repo-local module
    card_path = out_dir / "model_card.md"
    card_path.write_text(gmc.render_markdown(gmc.build_card(
        model_id=os.getenv("AALI_MODEL_ID", "aali"),
        version=source.name.replace(".pt", ""),
        config=cfg)), encoding="utf-8")
    out_files.append(card_path)
    print(f"[convert] wrote {len(out_files)} files to {out_dir}")
    return out_files


def validate_adapter_dir(adapter_dir: Path) -> Path:
    """Check the tuned-checkpoint layout the soup pipeline produces."""
    if not (adapter_dir / "adapter_model.safetensors").exists():
        _die(f"{adapter_dir} has no adapter_model.safetensors — "
             "this looks like a full-model dir; use --convert-only flow")
    if not (adapter_dir / "adapter_config.json").exists():
        _die(f"{adapter_dir} has no adapter_config.json")
    return adapter_dir


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_dir(target_dir: Path, repo_id: str, *, private: bool,
               force: bool = False) -> None:
    """Upload every file in target_dir to HF, skipping unchanged files."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        _die("huggingface_hub is required for --upload "
             "(pip install huggingface_hub)")
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        _die("HF_TOKEN is not set — refusing to touch the Hub")
    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, private=private, exist_ok=True)
    for path in sorted(target_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            info = api.get_paths_info(repo_id, path.name, repo_type="model")
            remote = (info or [{}])[0].get("lfs") or {}
            same = info and info[0].get("oid") == sha256_file(path)
        except Exception:
            same = False
        if same and not force:
            print(f"[upload] skip (unchanged): {path.name}")
            continue
        print(f"[upload] {path.name} "
              f"({path.stat().st_size / 1e6:.1f} MB)")
        api.upload_file(
            path_or_fileobj=str(path), path_in_repo=path.name,
            repo_id=repo_id, repo_type="model")
    print(f"[upload] done → https://huggingface.co/{repo_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert Aali checkpoints for HuggingFace and upload "
                    "(network upload gated by AALI_UPLOAD_ENABLED=1)")
    parser.add_argument("--model", required=True,
                        help="checkpoint dir (final.pt/checkpoint.pt or a "
                             "soup adapter dir)")
    parser.add_argument("--repo", default="",
                        help="target HF repo, e.g. HWK/aali-110m")
    parser.add_argument("--private", action="store_true", default=True,
                        help="create the repo private (default)")
    parser.add_argument("--public", action="store_true",
                        help="make the repo public (needs explicit intent)")
    parser.add_argument("--convert-only", action="store_true",
                        help="convert locally, no network at all")
    parser.add_argument("--out", default="",
                        help="conversion output dir (default <model>-hf)")
    parser.add_argument("--force", action="store_true",
                        help="re-upload even when files are unchanged")
    args = parser.parse_args(argv)

    model_dir = Path(args.model)
    if not model_dir.is_dir():
        _die(f"model dir not found: {model_dir}")

    adapter_layout = (model_dir / "adapter_model.safetensors").exists()
    out_dir = Path(args.out) if args.out else model_dir.parent / \
        f"{model_dir.name}-hf"

    if adapter_layout:
        print(f"[info] {model_dir.name} is a soup adapter dir "
              "(LoRA safetensors); uploading as-is (no conversion).")
        target = validate_adapter_dir(model_dir)
    else:
        target = out_dir
        convert_full_model(model_dir, out_dir)

    if args.convert_only:
        print("[done] --convert-only: no network used.")
        return 0

    repo_id = args.repo or os.getenv("AALI_HF_REPO", "")
    if not repo_id:
        _die("--repo (or AALI_HF_REPO) is required for upload")

    # ————— THE GATE —————
    if os.getenv("AALI_UPLOAD_ENABLED", "").strip() != "1":
        print("AALI_UPLOAD_ENABLED is not set to 1 — upload refused.\n"
              "The owner approves every publication: set AALI_UPLOAD_ENABLED=1 "
              "and HF_TOKEN, then re-run.")
        return 3

    upload_dir(target, repo_id, private=not args.public, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
