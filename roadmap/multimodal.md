# Multimodal & File-Format Roadmap (HWK Aali)

What the user asked: the model should read images, generate and edit images,
view videos, and handle "all file types". This doc is the honest plan split into
three columns: **done now**, **buildable on this PC (8 GB VRAM)**, and
**needs more hardware or a cloud GPU**.

## 1. Reading images (image → understanding)

| Capability | Status |
|---|---|
| Extract text from an image (OCR) | ✅ **DONE** — new agent tool `read_image` uses the Windows built-in OCR engine (win_ocr.ps1). Verified: generated PNG with 3 text lines → all 3 read back. Works for screenshots, photos of documents/signs, and any image whose *text* matters. Arabic depends on the Windows OCR language pack installed on the PC. |
| Understand image *content* (objects, scenes, layout) | ⏳ Real vision-language (VLM). Needs a vision encoder (SigLIP/CLIP) + a projector trained to map image embeddings into our token space + multimodal training data. Feasible as a *later fine-tune on Colab/a cloud GPU* (free tier is too small for the full stack, but the projector + adapter training is small). Architecture must be added to the model first (see §4). |

Practical path until the VLM exists: user drops an image path into the chat →
agent calls `read_image` → model reasons about the extracted text. If the image
has no text, the honest answer is "I can't see pictures yet — send the text or
describe it". No pretending.

## 2. Generating & editing pictures

| Capability | Status |
|---|---|
| Text → image, image → image (edit), inpainting | ✅ **BUILT & VERIFIED** — Stable Diffusion 1.4 in a **separate venv** (`D:\hwk-tools\sd-venv`; training venv untouched). `scripts/image_tools.py gen|edit` → txt2img and img2img. Tested locally: 15-step 384² image in **3.4 s** (load 77 s). Outputs go to `D:\hwk-projects\images\`. Run via `scripts\run_image_tools.bat`. |
| Native image *generation* inside our own model | ⏳ Years-scale from scratch. Never pretend otherwise. The local model learns to *write* (prompts, descriptions); SD does the pixels. |
| The model describing/editing via its own weights | Later — teach it prompt-crafting in Phase C with image-caption data (e.g., download a caption dataset). |

## 3. Viewing videos & "all file types"

| Capability | Status |
|---|---|
| "All file types" — the app can ingest | ⏳ Buildable list of text extractors the agent can already call or read natively: `.txt .md .py .js .json .csv .html .xml .log` (read_file) · `.png .jpg` (read_image) · `.pdf` (needs a text-extract tool — pdfminer/pypdf in a side venv) · `.docx` (python-docx) · `.xlsx` (openpyxl) · `.zip` (unzip + read contents). Most are small pure-python libs — safe to add. |
| Video | ⏳ Two honest stages: **(a) now:** extract frames at N-second intervals → OCR each frame (done above) → the model reads the "subtitles" of the video; audio track → Whisper (small model fits on this GPU) gives the spoken transcript. That gives real, useful video *understanding* of text+speech. **(b) later:** true video-language models need 10× the memory — cloud GPU only. |

## 4. What our model itself needs (architecture) for real multimodality

1. Add a **vision tower input**: a frozen SigLIP encoder + a small trainable
   MLP projector producing embeddings our transformer can attend to. Trained on
   image-caption pairs (CC3M subset etc.) — this is the cheapest real-VLM path
   and fits on Colab GPU in a few days of fine-tuning *after* Phase A/B/C.
2. RoPE is already in place and length-extendable — good foundation; the same
   backbone stays trainable for the projector stage.

## Priority recommendation

1. ✅ `read_image` (done, verified today).
2. 🔜 PDF/Office text extractors + video frame→OCR + Whisper transcript tool
   (pure-python, safe, all on this PC) — gives "sees documents & videos".
3. 🔜 Stable Diffusion in a separate venv for generate/edit pictures.
4. 🔜 Projector+VLM fine-tune on Colab after the language training phases —
   the only path to the model *understanding* pixels in its own weights.

Scheduling note: SD and Whisper experiments use the GPU; Phase A pretraining
(once Arabic tokenization finishes) also wants the GPU. Best order: run SD/Whisper
builds **while CPU tokenization is still running**, then hand the GPU to Phase A.
