"""Fast media editing for the HWK agent: Pillow for images, ffmpeg for video/audio.

Called by file_agent/file_tools.py (edit_image / edit_video) in a subprocess so
heavy work never blocks the agent process. Every path it touches is resolved
and sandboxed by the tool layer before it gets here.

Usage:
  python scripts/media_edit.py image --op resize --input a.png --output b.png --width 300 --height 200
  python scripts/media_edit.py video --op cut --input a.mp4 --output b.mp4 --start 1.0 --end 3.5
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
IMAGE_OPS = {
    "resize", "crop", "rotate", "flip", "mirror", "grayscale",
    "brightness", "contrast", "saturation", "blur", "sharpen",
    "border", "watermark_text", "thumbnail",
}
VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".webm"}
VIDEO_OPS = {
    "cut", "trim", "concat", "gif", "extract_audio", "extract_frame",
    "speed", "volume", "resize", "crop", "rotate", "flip", "mirror",
    "fade", "watermark_image",
}


def _fail(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False))
    sys.exit(1)


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("D:/hwk-tools/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/bin/ffmpeg.exe"):
        if Path(candidate).is_file():
            return candidate
    _fail("ffmpeg not found on PATH")


def _ffprobe(path: Path) -> dict:
    probe = shutil.which("ffprobe") or str(Path(_ffmpeg()).with_name("ffprobe.exe"))
    proc = subprocess.run(
        [probe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout).get("format", {})
    except json.JSONDecodeError:
        return {}


def _run_ffmpeg(args: list[str], output: Path, timeout: int = 900) -> None:
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args, str(output)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        _fail("ffmpeg failed: " + (proc.stderr or proc.stdout or "")[-400:])


def _edit_image(args: argparse.Namespace) -> dict:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageFilter

    source = Image.open(args.input)
    image = source
    degrees = {"rotate": 90}.get(args.op, 0)

    if args.op == "resize":
        image = source.resize((args.width, args.height))
    elif args.op == "thumbnail":
        source.thumbnail((args.width, args.height))
        image = source
    elif args.op == "crop":
        box = (args.left, args.top, args.left + args.width, args.top + args.height)
        if box[2] > source.width or box[3] > source.height:
            _fail(f"crop box {box} exceeds image size {source.size}")
        image = source.crop(box)
    elif args.op == "rotate":
        image = source.rotate(args.deg, expand=True)
    elif args.op == "flip":
        image = source.transpose(Image.FLIP_TOP_BOTTOM)
    elif args.op == "mirror":
        image = source.transpose(Image.FLIP_LEFT_RIGHT)
    elif args.op == "grayscale":
        image = source.convert("L").convert("RGB")
    elif args.op == "brightness":
        image = ImageEnhance.Brightness(source).enhance(args.factor)
    elif args.op == "contrast":
        image = ImageEnhance.Contrast(source).enhance(args.factor)
    elif args.op == "saturation":
        image = ImageEnhance.Color(source).enhance(args.factor)
    elif args.op == "blur":
        image = source.filter(ImageFilter.GaussianBlur(radius=args.radius))
    elif args.op == "sharpen":
        image = source.filter(ImageFilter.UnsharpMask(radius=args.radius, percent=150))
    elif args.op == "border":
        image = ImageOps_expand(source, args.width)
    elif args.op == "watermark_text":
        image = source.convert("RGB").copy()
        draw = ImageDraw.Draw(image)
        font = None
        for candidate in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/tahoma.ttf"):
            try:
                font = ImageFont.truetype(candidate, max(16, image.height // 18))
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        text = args.text or ""
        if not text:
            _fail("watermark_text needs --text")
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        margin = max(8, image.width // 50)
        position = (image.width - (right - left) - margin, image.height - (bottom - top) - margin)
        draw.text(position, text, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"quality": 92} if args.output.suffix.lower() in {".jpg", ".jpeg"} else {}
    image.save(args.output, **save_kwargs)
    return {"op": args.op, "input_size": list(source.size), "output_size": list(image.size)}


def ImageOps_expand(source, width: int):
    from PIL import ImageOps

    return ImageOps.expand(source, border=width, fill=(255, 255, 255))


def _edit_video(args: argparse.Namespace) -> dict:
    src = str(args.input)
    probe = _ffprobe(args.input)
    duration = float(probe.get("duration", 0) or 0)
    tail = ["-movflags", "+faststart"] if args.output.suffix.lower() == ".mp4" else []

    if args.op == "cut":
        if args.end is not None and args.end <= args.start:
            _fail("--end must be greater than --start")
        if duration and args.start >= duration:
            _fail(f"--start {args.start}s is beyond the clip length ({duration:.1f}s)")
        _run_ffmpeg(["-ss", str(args.start), "-i", src, "-t", str(args.duration), "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", *tail], args.output)
    elif args.op == "trim":
        _run_ffmpeg(["-i", src, "-t", str(args.duration), "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", *tail], args.output)
    elif args.op == "concat":
        if not args.input2:
            _fail("concat needs --input2 (the second clip)")
        listing = args.output.with_suffix(".concat.txt")
        listing.write_text(f"file '{src}'\nfile '{args.input2}'\n", encoding="utf-8")
        try:
            _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", *tail], args.output)
        finally:
            listing.unlink(missing_ok=True)
    elif args.op == "gif":
        _run_ffmpeg(["-ss", str(args.start), "-t", str(args.duration), "-i", src,
                     "-vf", "fps=10,scale=320:-1:flags=lanczos", "-loop", "0"], args.output)
    elif args.op == "extract_audio":
        if args.output.suffix.lower() != ".mp3":
            _fail("extract_audio output must end in .mp3")
        _run_ffmpeg(["-i", src, "-vn", "-acodec", "libmp3lame", "-q:a", "4"], args.output)
    elif args.op == "extract_frame":
        if args.output.suffix.lower() not in IMAGE_SUFFIXES:
            _fail("extract_frame output must end in .png/.jpg/.jpeg")
        _run_ffmpeg(["-ss", str(args.start), "-i", src, "-frames:v", "1"], args.output)
    elif args.op == "speed":
        if not 0.25 <= args.factor <= 4:
            _fail("--factor must be between 0.25 and 4")
        atempo = max(0.5, min(2.0, args.factor))
        _run_ffmpeg(["-i", src, "-filter:v", f"setpts=PTS/{args.factor}", "-filter:a", f"atempo={atempo}",
                     "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", *tail], args.output)
    elif args.op == "volume":
        if not 0 <= args.factor <= 2:
            _fail("--factor must be between 0 and 2 (1 = unchanged)")
        _run_ffmpeg(["-i", src, "-filter:a", f"volume={args.factor}", "-c:v", "copy", "-c:a", "aac", *tail], args.output)
    elif args.op == "resize":
        _run_ffmpeg(["-i", src, "-vf", f"scale={args.width}:{args.height}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    elif args.op == "crop":
        _run_ffmpeg(["-i", src, "-vf", f"crop={args.width}:{args.height}:{args.left}:{args.top}",
                     "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    elif args.op == "rotate":
        deg = args.deg % 360
        if deg == 90:
            transpose = "transpose=1"
        elif deg == 180:
            transpose = "transpose=1,transpose=1"
        elif deg == 270:
            transpose = "transpose=2"
        else:
            _fail("--deg must be 90, 180, or 270 for video")
        _run_ffmpeg(["-i", src, "-vf", transpose, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    elif args.op == "flip":
        _run_ffmpeg(["-i", src, "-vf", "vflip", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    elif args.op == "mirror":
        _run_ffmpeg(["-i", src, "-vf", "hflip", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    elif args.op == "fade":
        if duration <= 1:
            _fail("clip too short for fades (need > 1s)")
        fade_out_start = max(0.0, duration - 0.5)
        _run_ffmpeg(["-i", src, "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start:.2f}:d=0.5",
                     "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    elif args.op == "watermark_image":
        if not args.input2:
            _fail("watermark_image needs --input2 (the PNG overlay)")
        position = {"top_left": "10:10", "top_right": "W-w-10:10",
                    "bottom_left": "10:H-h-10", "bottom_right": "W-w-10:H-h-10"}.get(args.position)
        if position is None:
            _fail("--position must be one of top_left/top_right/bottom_left/bottom_right")
        _run_ffmpeg(["-i", src, "-i", str(args.input2), "-filter_complex", f"overlay={position}",
                     "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", *tail], args.output)
    else:
        _fail(f"unknown video op: {args.op}")

    return {"op": args.op, "source_duration": round(duration, 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description="HWK fast media editing (Pillow + ffmpeg)")
    parser.add_argument("kind", choices=["image", "video"])
    parser.add_argument("--op", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--input2", type=Path, default=None, help="second input (concat clip / watermark image)")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--left", type=int, default=0)
    parser.add_argument("--top", type=int, default=0)
    parser.add_argument("--deg", type=int, default=0)
    parser.add_argument("--factor", type=float, default=1.0)
    parser.add_argument("--radius", type=float, default=2.0)
    parser.add_argument("--text", default="")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--position", default="bottom_right")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    for path in (args.input, args.input2):
        if path is not None and not path.is_file():
            _fail(f"input not found: {path}")
    if args.output.exists():
        _fail(f"output already exists: {args.output}")

    allowed = IMAGE_OPS if args.kind == "image" else VIDEO_OPS
    if args.op not in allowed:
        _fail(f"op '{args.op}' is not a {args.kind} op (allowed: {sorted(allowed)})")
    if args.kind == "image" and args.input.suffix.lower() not in IMAGE_SUFFIXES:
        _fail("image input must be .png/.jpg/.jpeg")
    if args.kind == "video" and args.input.suffix.lower() not in VIDEO_SUFFIXES:
        _fail(f"video input must be one of {sorted(VIDEO_SUFFIXES)}")

    if args.op == "resize" and args.kind == "image" and (args.width <= 0 or args.height <= 0):
        _fail("resize needs positive --width and --height")
    if args.op == "thumbnail" and (args.width <= 0 or args.height <= 0):
        _fail("thumbnail needs positive --width and --height")
    if args.op == "rotate" and args.kind == "image" and args.deg % 90 != 0:
        _fail("image --deg must be a multiple of 90")
    if args.op in {"cut", "trim", "gif"} and (args.duration is None or args.duration <= 0):
        _fail(f"{args.op} needs a positive --duration")
    if args.op in {"brightness", "contrast", "saturation", "speed", "volume"} and not 0 < args.factor <= 4:
        _fail(f"{args.op} --factor must be in (0, 4]")

    started = time.time()
    if args.kind == "image":
        info = _edit_image(args)
    else:
        info = _edit_video(args)
    info.update({
        "input": str(args.input),
        "output": str(args.output),
        "output_bytes": args.output.stat().st_size,
        "elapsed_seconds": round(time.time() - started, 1),
        "tool": "media_edit",
    })
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
