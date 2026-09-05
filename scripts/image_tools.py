"""HWK image tools: text-to-image and image-editing with Stable Diffusion.

Run with the SD venv's python but the project's CUDA torch on PYTHONPATH so we
never touch the training environment:

    PYTHONPATH=D:/hwk-data/.venv-ignore PATH_PLACEHOLDER  (see run_image_tools.bat)

Commands:
  python scripts/image_tools.py gen "a red fox in snow" --out D:/hwk-projects/images/fox.png
  python scripts/image_tools.py edit "make it night time" --image in.png --out out.png
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

MODEL_ID = os.getenv("HWK_SD_MODEL", "CompVis/stable-diffusion-v1-4")
CACHE_DIR = Path(os.getenv("HWK_SD_CACHE", "D:/hwk-data/sd-models"))


def _load_pipeline(kind: str):
    import torch
    from diffusers import AutoPipelineForText2Image, AutoPipelineForImage2Image

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[sd] device={device} model={MODEL_ID} pipeline={kind}", flush=True)
    dtype = torch.float16 if device == "cuda" else torch.float32
    pipe_class = AutoPipelineForImage2Image if kind == "edit" else AutoPipelineForText2Image
    pipe = pipe_class.from_pretrained(
        MODEL_ID, torch_dtype=dtype, cache_dir=str(CACHE_DIR), safety_checker=None
    )
    pipe = pipe.to(device)
    if getattr(pipe, "enable_attention_slicing", None):
        try:
            pipe.enable_attention_slicing()
        except Exception:  # noqa: BLE001
            pass
    return pipe


def generate(prompt: str, out: Path, *, steps: int = 24, width: int = 512,
             height: int = 512, seed: int | None = None) -> None:
    pipe = _load_pipeline("gen")
    seed = seed if seed is not None else random.randint(0, 2**31)
    generator = _seed_generator(seed)
    start = time.time()
    image = pipe(
        prompt=prompt, num_inference_steps=steps,
        width=width, height=height, generator=generator,
        negative_prompt="blurry, low quality, distorted",
    ).images[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(f"[sd] saved {out} ({time.time() - start:.1f}s, seed={seed})", flush=True)


def edit(prompt: str, image_in: Path, out: Path, *, steps: int = 24,
         strength: float = 0.65, seed: int | None = None) -> None:
    pipe = _load_pipeline("edit")
    seed = seed if seed is not None else random.randint(0, 2**31)
    generator = _seed_generator(seed)
    from PIL import Image as PILImage

    source = PILImage.open(image_in).convert("RGB")
    start = time.time()
    image = pipe(
        prompt=prompt, image=source, num_inference_steps=steps,
        strength=strength, generator=generator,
        negative_prompt="blurry, low quality, distorted",
    ).images[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(f"[sd] saved {out} ({time.time() - start:.1f}s, seed={seed})", flush=True)


def _seed_generator(seed: int):
    import torch

    return torch.Generator(device="cpu").manual_seed(seed)


def main() -> None:
    parser = argparse.ArgumentParser(description="HWK Stable Diffusion image tools")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen", help="text → image")
    gen.add_argument("prompt")
    gen.add_argument("--out", required=True)
    gen.add_argument("--steps", type=int, default=24)
    gen.add_argument("--width", type=int, default=512)
    gen.add_argument("--height", type=int, default=512)
    gen.add_argument("--seed", type=int, default=None)

    edit_p = sub.add_parser("edit", help="edit an existing image (img2img)")
    edit_p.add_argument("prompt")
    edit_p.add_argument("--image", required=True)
    edit_p.add_argument("--out", required=True)
    edit_p.add_argument("--steps", type=int, default=24)
    edit_p.add_argument("--strength", type=float, default=0.65)
    edit_p.add_argument("--seed", type=int, default=None)

    args = parser.parse_args()
    if args.command == "gen":
        generate(args.prompt, Path(args.out), steps=args.steps,
                 width=args.width, height=args.height, seed=args.seed)
    else:
        edit(args.prompt, Path(args.image), Path(args.out),
             steps=args.steps, strength=args.strength, seed=args.seed)


if __name__ == "__main__":
    main()
