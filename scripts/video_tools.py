"""HWK video tools: understand a video by reading frames (OCR) and its audio
track (Whisper transcript). Called by the agent's analyze_video tool.

Frames come from OpenCV's built-in decoder (no ffmpeg needed for video);
audio extraction needs ffmpeg (installed via winget when available).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

OCR_SCRIPT = Path(__file__).resolve().parents[1] / "win_ocr.ps1"


def _frame_ocr(image_path: Path) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(OCR_SCRIPT), str(image_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if proc.returncode != 0:
            return ""
        items = json.loads(proc.stdout or "[]")
        if isinstance(items, dict):  # ConvertTo-Json unwraps single-element arrays
            items = [items]
        return "\n".join(str(item.get("text", "")).strip() for item in items if item.get("text")).strip()
    except Exception:  # noqa: BLE001
        return ""


def analyze_video(path: Path, *, frame_interval: float = 5.0, max_frames: int = 12,
                  transcript: bool = True, whisper_model: str = "small") -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "frames_ocr": [], "transcript": None}

    # — 1. frames → OCR —
    try:
        import cv2

        capture = cv2.VideoCapture(str(path))
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        result["duration_sec"] = round(total / fps, 1) if fps else None
        step = max(1, int(fps * frame_interval))
        picked, index = [], 0
        while len(picked) < max_frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                break
            picked.append(frame)
            index += step
        capture.release()

        with tempfile.TemporaryDirectory() as tmp:
            for i, frame in enumerate(picked):
                stamp = round(i * frame_interval, 1)
                image_path = Path(tmp) / f"frame_{i:03d}.png"
                if not cv2.imwrite(str(image_path), frame):
                    continue
                text = _frame_ocr(image_path)
                if text:
                    result["frames_ocr"].append({"at_sec": stamp, "text": text})
        result["frames_sampled"] = len(picked)
    except ImportError:
        result["frames_error"] = "opencv-python غير مثبت"
    except Exception as exc:  # noqa: BLE001
        result["frames_error"] = f"{type(exc).__name__}: {exc}"

    # — 2. audio → transcript (needs ffmpeg on PATH) —
    if transcript:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            try:
                import imageio_ffmpeg

                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:  # noqa: BLE001
                ffmpeg = None
        if not ffmpeg:
            result["transcript_error"] = "ffmpeg not found (winget install Gyan.FFmpeg)"
        else:
            _transcribe(path, result, ffmpeg, whisper_model)

    return result


def _transcribe(path: Path, result: dict[str, Any], ffmpeg: str, whisper_model: str) -> None:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                capture_output=True, timeout=300,
            )
            if proc.returncode == 0 and wav.exists():
                from faster_whisper import WhisperModel

                model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
                segments, info = model.transcribe(str(wav), vad_filter=True)
                lines = [
                    f"[{seg.start:6.1f}s] {seg.text.strip()}"
                    for seg in segments
                    if seg.text.strip()
                ]
                result["transcript"] = {
                    "language": info.language,
                    "lines": lines[:400],
                }
            else:
                result["transcript_error"] = "ffmpeg could not extract audio"
    except Exception as exc:  # noqa: BLE001
        result["transcript_error"] = f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Understand a video: frame OCR + audio transcript")
    parser.add_argument("video")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--transcript", dest="transcript", action="store_true", default=True)
    parser.add_argument("--no-transcript", dest="transcript", action="store_false")
    parser.add_argument("--whisper-model", default="small")
    args = parser.parse_args()

    start = time.time()
    report = analyze_video(
        Path(args.video),
        frame_interval=args.interval,
        max_frames=args.max_frames,
        transcript=args.transcript,
        whisper_model=args.whisper_model,
    )
    print(json.dumps(report, ensure_ascii=False))
    print(f"--- {time.time() - start:.1f}s ---", file=sys.stderr)
