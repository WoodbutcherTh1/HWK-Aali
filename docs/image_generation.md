# Image generation (`generate_image` tool)

Aali's language model is text-only and always will be - no amount of
fine-tuning gives a transformer the ability to output pixels. What *is*
real: a separate local Stable Diffusion model that the agent can call as a
tool, the same way it already calls `read_image`, `fetch_url`, etc.

## What was added

- `scripts/image_gen_tool.py` - standalone script (subprocess-only, same
  pattern as `scripts/video_tools.py`) that loads
  `runwayml/stable-diffusion-v1-5` via `diffusers` and renders a PNG/JPG from
  a text prompt.
- `generate_image` tool in `file-agent/file_agent/file_tools.py` - resolves
  the output path inside the workspace, shells out to the script above, and
  returns `{"path", "model", "device", "width", "height", "steps"}`.
- `diffusers>=0.30,<1` added to `requirements.txt` (torch/accelerate/
  huggingface-hub were already dependencies).

## First run

The model weights (~2GB) download from Hugging Face the first time
`generate_image` is called - this needs a normal internet connection on
your machine (not something that happens through any restricted sandbox).
After that first download they're cached locally and every later call is
fast.

## GPU sharing with training

`generate_image` uses the same 8GB card Phase A pretraining is using.
Expect one of:
- It runs fine alongside training if there's enough free VRAM (Aali's own
  model is small - the SD pipeline in fp16 needs roughly 4-5GB).
- It hits `out of memory`, in which case the tool automatically retries on
  CPU (correct output, just much slower - minutes instead of seconds).

For anything more than occasional use, it's more efficient to close the
training window first, generate images, then reopen `resume_training.bat`.

## License note

`runwayml/stable-diffusion-v1-5` ships under the CreativeML OpenRAIL-M
license. Images you generate are yours to use; if you ever redistribute the
model weights themselves, read the license first:
https://huggingface.co/spaces/CompVis/stable-diffusion-license

## Video (`generate_video`)

Same pattern, one tier down in ambition: `scripts/video_gen_tool.py` runs a
small open text-to-video model (`damo-vilab/text-to-video-ms-1.7b`,
~1.7B params) fully locally. State this plainly every time it comes up:
this is **not** Sora/Veo/Runway-class output. It produces short (a couple
of seconds), low-resolution (256x256 by default), sometimes rough clips -
real and fully local, just small. `enable_model_cpu_offload()` is used
(swaps unused submodules to system RAM) to make a 1.7B video model fit
alongside Aali's own training on the same 8GB card at all; if it still hits
`out of memory`, the tool raises a clear message asking you to close
`resume_training.bat` first rather than silently taking forever on CPU.

Both `generate_image` and `generate_video`'s tool *descriptions* explicitly
tell the model to disclose these limitations rather than oversell the
output - and `data/tool_calling_sft.jsonl` now has worked examples (Arabic
and English) of Aali doing exactly that when a user asks for something like
a "professional ad video."
