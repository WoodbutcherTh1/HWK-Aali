---
name: media-tools
description: Reading text from images/scans (OCR), understanding video/audio (frames + transcript), and reading document files (pdf/docx/xlsx). Use before asking the user to describe media themselves.
---

## When to use this

Any time the task involves a file that isn't plain text — a screenshot, a scanned document, a photo of a whiteboard, an audio/video recording, a PDF, a Word or Excel file — reach for these tools yourself instead of asking the user to transcribe or describe it.

## Tools

- `read_image(path)` — OCRs the text inside an image. Use for screenshots, photographed documents, signs. You get the text back, not a pixel description — if the user needs a *visual* description (colors, layout, what's drawn), say plainly that's outside what this tool gives you.
- `read_document(path)` — extracts text from `.pdf`, `.docx`, `.xlsx`. Use this instead of trying to open the raw bytes with `read_file`.
- `analyze_video(path)` — samples frames for on-screen text (OCR) and transcribes the audio track (Whisper) in one call. Use `frame_interval`/`max_frames` to control cost on long videos, and set `transcript=False` if you only need on-screen text.

## ffmpeg, under the hood

`analyze_video` already shells out to ffmpeg to extract frames and audio — you never need to construct an `ffmpeg` command yourself for basic "what's in this video/what does it say" questions. If the user asks for an actual *edit* (trim, convert format, extract just the audio as a file, resize, merge clips), that's a legitimate `run_command` use with `ffmpeg` directly — ffmpeg is expected to be on PATH; if a command fails with "not found", say so plainly rather than guessing at a workaround.

Typical direct-ffmpeg patterns worth knowing:
- Trim: `ffmpeg -i in.mp4 -ss 00:00:10 -to 00:00:40 -c copy out.mp4`
- Extract audio: `ffmpeg -i in.mp4 -vn -acodec copy out.aac`
- Convert: `ffmpeg -i in.mov out.mp4`

Always run these through `run_command` (never invent a shell you don't have) and confirm the output file was actually created before telling the user it worked.
