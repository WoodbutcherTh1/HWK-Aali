"""HWK video generation: text prompt -> short MP4 clip, via a local
text-to-video diffusion model. Called by the agent's generate_video tool.

Honest limitation, stated up front because it matters: this is nowhere near
Sora/Veo/Runway-class output. Those run on data-center GPU clusters trained
on hundreds of millions of video clips. This runs a much smaller open model
(damo-vilab/text-to-video-ms-1.7b) on a single consumer GPU, and produces
short (a couple of seconds), low-resolution, often rough clips. It is a
real, fully local, no-API text-to-video pipeline - just a small one.

Runs as its own subprocess (torch + diffusers stay out of the main agent
process), same pattern as image_gen_tool.py / video_tools.py.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "damo-vilab/text-to-video-ms-1.7b"


def generate_video(
    prompt: str,
    output_path: Path,
    *,
    negative_prompt: str = "",
    num_frames: int = 16,
    steps: int = 25,
    guidance_scale: float = 9.0,
    height: int = 256,
    width: int = 256,
    fps: int = 8,
    seed: int | None = None,
    model_id: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    import torch
    from diffusers import DiffusionPipeline
    from diffusers.utils import export_to_video

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = DiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
    if device == "cuda":
        # Model-CPU-offload keeps only the submodule currently running on the
        # GPU, swapping the rest to system RAM - this is what makes a ~1.7B
        # video model fit alongside Aali's own training on the same 8GB card
        # at all. Slower than a plain .to("cuda"), but far less likely to OOM.
        pipe.enable_model_cpu_offload()
        pipe.enable_attention_slicing()
    else:
        pipe = pipe.to("cpu")

    generator = None
    if seed is not None:
        generator = torch.Generator(device="cpu").manual_seed(seed)

    try:
        result = pipe(
            prompt,
            negative_prompt=negative_prompt or None,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            num_frames=num_frames,
            height=height,
            width=width,
            generator=generator,
        )
    except RuntimeError as exc:
        if device == "cuda" and "out of memory" in str(exc).lower():
            raise RuntimeError(
                "Out of GPU memory. Aali's own training is likely using most of the "
                "8GB card right now - close resume_training.bat and try again."
            ) from exc
        raise

    video_frames = result.frames[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(video_frames, output_video_path=str(output_path), fps=fps)

    return {
        "path": str(output_path),
        "model": model_id,
        "device": device,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "fps": fps,
        "duration_seconds": round(num_frames / fps, 1),
        "note": "Short/low-res local clip - not comparable to commercial video generators.",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a short video clip from a text prompt (local text-to-video)")
    parser.add_argument("prompt")
    parser.add_argument("--output", required=True)
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument("--num-frames", type=int, default=16)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--guidance", type=float, default=9.0)
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    start = time.time()
    try:
        report = generate_video(
            args.prompt, Path(args.output),
            negative_prompt=args.negative_prompt, num_frames=args.num_frames,
            steps=args.steps, guidance_scale=args.guidance,
            width=args.width, height=args.height, fps=args.fps,
            seed=args.seed, model_id=args.model,
        )
    except ImportError as exc:
        print(json.dumps({
            "error": "missing_dependency",
            "message": f"{exc}. Install with: pip install diffusers accelerate opencv-python",
        }))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        sys.exit(1)

    print(json.dumps(report, ensure_ascii=False))
    print(f"--- {time.time() - start:.1f}s ---", file=sys.stderr)
