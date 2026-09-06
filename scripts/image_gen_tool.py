"""HWK image generation: text prompt -> PNG file, via a local Stable Diffusion
model. Called by the agent's generate_image tool.

Runs as its own subprocess (torch + diffusers are heavy imports and this
model is a separate ~2GB download on first use) so the main agent process
stays light, matching the pattern used by analyze_video/video_tools.py.

Model: runwayml/stable-diffusion-v1-5 (CreativeML OpenRAIL-M license - the
generated images are the user's, but redistribution of the *weights* carries
usage restrictions worth reading once: https://huggingface.co/spaces/CompVis/stable-diffusion-license).
Downloaded and cached locally the first time this runs (needs a real internet
connection at that point - it does not happen from inside any restricted
sandbox, only when this script executes directly on the user's machine).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "runwayml/stable-diffusion-v1-5"


def generate_image(
    prompt: str,
    output_path: Path,
    *,
    negative_prompt: str = "",
    steps: int = 25,
    guidance_scale: float = 7.5,
    width: int = 512,
    height: int = 512,
    seed: int | None = None,
    model_id: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    import torch
    from diffusers import StableDiffusionPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = StableDiffusionPipeline.from_pretrained(
        model_id, torch_dtype=dtype, safety_checker=None,
    )
    pipe = pipe.to(device)
    if device == "cuda":
        pipe.enable_attention_slicing()  # lower peak VRAM, matters since Aali's
        # own training may already be using most of the 8GB card.

    generator = None
    if seed is not None:
        generator = torch.Generator(device=device).manual_seed(seed)

    def _run() -> Any:
        return pipe(
            prompt,
            negative_prompt=negative_prompt or None,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
            generator=generator,
        ).images[0]

    try:
        image = _run()
    except RuntimeError as exc:
        if device == "cuda" and "out of memory" in str(exc).lower():
            # The training run (or another process) is likely holding most of
            # the 8GB card. Fall back to CPU rather than fail outright - slow
            # (minutes, not seconds) but it will finish.
            torch.cuda.empty_cache()
            device = "cpu"
            pipe = pipe.to("cpu")
            image = _run()
        else:
            raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return {
        "path": str(output_path),
        "model": model_id,
        "device": device,
        "width": width,
        "height": height,
        "steps": steps,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate an image from a text prompt (local Stable Diffusion)")
    parser.add_argument("prompt")
    parser.add_argument("--output", required=True)
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--guidance", type=float, default=7.5)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    start = time.time()
    try:
        report = generate_image(
            args.prompt, Path(args.output),
            negative_prompt=args.negative_prompt, steps=args.steps,
            guidance_scale=args.guidance, width=args.width, height=args.height,
            seed=args.seed, model_id=args.model,
        )
    except ImportError as exc:
        print(json.dumps({
            "error": "missing_dependency",
            "message": f"{exc}. Install with: pip install diffusers accelerate",
        }))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        sys.exit(1)

    print(json.dumps(report, ensure_ascii=False))
    print(f"--- {time.time() - start:.1f}s ---", file=sys.stderr)
