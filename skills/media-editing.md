---
name: media-editing
description: Fast image and video editing with edit_image / edit_video (Pillow + ffmpeg) — resize, crop, rotate, cut, GIF, audio extraction, speed, fades, watermarks. Use for "crop this", "make a GIF", "cut the first 10 seconds", "add my name to the photo".
---

## The two tools

- `edit_image(path, output, op, ...)` — Pillow, instant (seconds).
- `edit_video(path, output, op, ...)` — ffmpeg, minutes at most.

Both refuse to overwrite: the `output` path must be NEW. If the user says
"عدّل نفس الصورة", write to `اسم-جديد.png` next to it and say so.

## Image ops (edit_image)

| op | extra params | example request |
|---|---|---|
| `resize` | width, height (required) | "خلّي الصورة 800×600" |
| `thumbnail` | width, height (fits inside) | "سوي منها صورة مصغرة 200" |
| `crop` | width, height, left, top | "اقطع الربع العلوي" |
| `rotate` | deg (90/180/270) | "لفها 90 درجة" |
| `flip` / `mirror` | — | "اقلبها" |
| `grayscale` | — | "خلّها أبيض وأسود" |
| `brightness` / `contrast` / `saturation` | factor (1.0 = unchanged; 1.3 = +30%) | "زيّد الإضاءة شوي" → factor 1.2 |
| `blur` / `sharpen` | radius | "طمس الخلفية" → blur radius 6 |
| `border` | width (pixels) | "حط إطار أبيض 20 بكسل" |
| `watermark_text` | text | "اكتب اسمي عالصورة" |

## Video ops (edit_video)

| op | extra params | example request |
|---|---|---|
| `cut` | start, duration (or end) | "اقطع من الثانية 5 لـ 10" |
| `trim` | duration | "شيل آخر ثانيتين" → duration = length-2 |
| `concat` | input2 (second clip) | "ركّب المقطعين" |
| `gif` | start, duration | "سويها GIF من الثانية 0 لـ 3" |
| `extract_audio` | output must be .mp3 | "طلّع لي الصوت" |
| `extract_frame` | start (seconds), output .png | "خذ لي صورة من الثانية 12" |
| `speed` | factor 0.25–4 | "سرّعها ضعفين" |
| `volume` | factor 0–2 | "خفّف الصوت نص" → 0.5 |
| `resize` / `crop` | width, height (+left, top) | "خلّي الفيديو 720p" |
| `rotate` / `flip` / `mirror` | deg (video: 90/180/270) | |
| `fade` | — | "حط تلاشي بالبداية والنهاية" |
| `watermark_image` | input2 = PNG overlay, position | "حط اللوقو بالزاوية" |

## How to work (self-verification applies)

1. If the file is a video and you need its length, `analyze_video` first —
   never guess timestamps.
2. Make the edit → the tool result confirms the output file exists and its
   size; report THAT, not your intention.
3. Big or unusual numbers (crop beyond the image, cut past the end) fail with
   a clear error — read the error, adjust, retry once, then tell the user
   what happened.
4. These tools EDIT pixels, they don't create content — generating a new
   image from text is `generate_image`, and both are honest about their
   limits (local SD, not commercial quality).
