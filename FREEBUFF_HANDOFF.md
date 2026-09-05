# Freebuff Handoff — HWK “Aali” Local AI File Agent

## 1. Mission

Continue building **Aali (آلي)** as a local, self-owned Arabic conversational
assistant that can:

1. Hold natural conversations:
   - “Hello” → a natural greeting.
   - Answer open-ended questions according to the conversation.
   - Preserve context during a chat session.
2. Safely operate on files:
   - List, read, create, append, replace, move, create directories, and delete.
   - Ask for/require explicit confirmation for dangerous operations such as
     overwriting and recursive deletion.
3. Run without:
   - OpenRouter.
   - Replit AI.
   - OpenAI, Anthropic, Qwen, GPT, or any other hosted model at runtime.
   - Any API key.

The user explicitly chose **training a model from scratch**, with random initial
weights and no pretrained base model. The phone/Replit phase is for coding,
dataset design, UI, tests, and small smoke tests. An RTX-class computer will be
needed later for serious training.

## 2. User expectations and constraints

- The user speaks Arabic and prefers clear, practical explanations.
- The user wants a real conversational assistant, not a handful of hard-coded
  question/answer pairs.
- The user wants to continue working from a phone before obtaining the RTX
  machine.
- Never ask the user to paste an API key or secret into chat.
- Do not silently replace the from-scratch plan with a hosted API or a
  pretrained model.
- The file agent must remain sandboxed inside its configured workspace.
- Never claim that a file operation succeeded if the tool returned an error.
- Preserve JSONL request logging in `file-agent/logs/agent.log`.

## 3. Current project layout

### Working application

- `file-agent/app.py`
  - Flask Arabic mobile-friendly UI.
  - Workflow command: `python file-agent/app.py`
  - Port: `5000`.
  - Current form has “local” and optional “cloud” mode controls.
  - Current UI is request/response, not yet a persistent chat transcript.

- `file-agent/agent_loop.py`
  - Current safe file-agent loop.
  - Contains a deterministic Arabic/English fallback parser.
  - Contains an older optional cloud path and an older `transformers.pipeline`
    local-model path.
  - It is **not yet wired to the new from-scratch `hwk_model` checkpoint**.
  - This is the highest-priority integration item.

- `file-agent/file_agent/file_tools.py`
  - The source of truth for file tools and safety checks.
  - Current tool names:
    - `list_files`
    - `read_file`
    - `write_file`
    - `append_file`
    - `replace_in_file`
    - `make_directory`
    - `move_file`
    - `delete_file`
  - `get_tool_definitions()` exposes the JSON schemas.
  - `execute_tool(name, arguments, workspace_root)` executes safely and
    returns `{ok: true, result: ...}` or `{ok: false, error: ...}`.

- `file-agent/agent_log.py`
  - Creates request IDs and appends JSONL lifecycle events.

- `test_agent.py`
  - No-key smoke test for create, read, append, and delete.

### Dataset and training files

- `create_training_data.py`
  - Creates Arabic conversation examples and Arabic file-tool examples.
  - Current tool/chat seed dataset contains 23 examples after regeneration.

- `data/agent_instructions.jsonl`
  - Generated JSONL seed dataset.
  - Contains `system`, `user`, and `assistant` messages.
  - Tool responses are compact JSON:

    ```json
    {"tool": "read_file", "arguments": {"path": "notes/today.txt"}}
    ```

- `file-agent/hwk_model/tokenizer.py`
  - Custom UTF-8 byte tokenizer.
  - No downloaded vocabulary.
  - Supports Arabic and arbitrary Unicode.
  - Vocabulary IDs:
    - bytes `0..255`
    - `PAD_ID=256`
    - `BOS_ID=257`
    - `EOS_ID=258`

- `file-agent/hwk_model/model.py`
  - Small causal Transformer implementation using PyTorch.
  - Random initialization.
  - Weight tying between token embedding and language-model head.
  - `ModelConfig` defaults:
    - context: 256
    - hidden size: 256
    - heads: 8
    - layers: 6

- `file-agent/hwk_model/generation.py`
  - Autoregressive generation for the scratch model.

- `file-agent/hwk_model/__init__.py`
  - Exports tokenizer, model, checkpoint, and generation primitives.

- `train_scratch.py`
  - From-scratch PyTorch training loop.
  - Supports:
    - JSONL or Parquet/text input.
    - train/evaluation split.
    - gradient accumulation.
    - CUDA fp16 autocast when CUDA exists.
    - checkpoint saving.
    - CSV logging to `scratch_training_log.csv`.
  - Default output: `model/scratch/`.
  - Final checkpoint: `model/scratch/final.pt`.

- `evaluate_scratch.py`
  - Calculates loss and perplexity for a scratch checkpoint.

### Existing legacy/transition files

- `train.py`
  - Older Transformers-based training script.
  - It still references a pretrained model workflow.
  - It should be replaced with a thin wrapper around `train_scratch.py` or
    clearly marked legacy before the project is considered consistent.

- `train_agent.py`
  - Older Qwen/Transformers instruction fine-tuning script.
  - It conflicts with the user’s explicit from-scratch requirement.
  - Do not use it as-is.
  - Replace it with a scratch-training wrapper or remove it.

- `evaluate.py`
  - Older Transformers-based evaluator.
  - Prefer `evaluate_scratch.py` for the current direction.

- `README.md`
  - Contains earlier instructions mentioning DistilGPT2, Qwen, and
    Transformers.
  - Must be rewritten so it describes only the from-scratch path, or clearly
    separates legacy experiments from the active path.

## 4. What is complete

- Sandboxed file tools with relative-path enforcement.
- Protection against escaping the workspace.
- Explicit safety flags for overwrite and recursive deletion.
- Arabic Flask interface.
- JSONL lifecycle logs.
- No-key deterministic fallback for basic file commands.
- Direct Parquet The Pile preparation script:
  - `prepare_data.py`
- Environment checker:
  - `check_env.py`
- Export/setup scripts:
  - `export_project.sh`
  - `setup_replit.sh`
- Arabic seed-data generator:
  - `create_training_data.py`
- Initial from-scratch tokenizer/model/training/evaluation implementation:
  - `file-agent/hwk_model/`
  - `train_scratch.py`
  - `evaluate_scratch.py`

## 5. What is incomplete and must be done next

### Priority 1 — integrate the scratch checkpoint into the agent

Modify `file-agent/agent_loop.py` so local mode:

1. Looks for `LOCAL_MODEL_PATH` if set.
2. Otherwise looks for `model/scratch/final.pt`.
3. Loads it with:

   ```python
   from hwk_model import ByteTokenizer, load_checkpoint
   from hwk_model.generation import generate_text
   ```

4. Generates a response using the current system prompt.
5. Parses a JSON tool call:

   ```json
   {"tool": "write_file", "arguments": {"path": "x.txt", "content": "..." }}
   ```

6. Executes the tool.
7. Adds the tool result back into the prompt.
8. Generates a final natural-language response.
9. Falls back to the deterministic parser only when no scratch checkpoint
   exists.

Do not make the Qwen/Transformers path the default.

### Priority 2 — add conversation memory

`file-agent/app.py` currently handles one POST as one isolated request.
Implement a real chat session:

- Use Flask session storage or a server-side session store.
- Use the existing `SESSION_SECRET` environment secret.
- Keep a bounded conversation history, for example the last 10–20 turns.
- Send system prompt + history + current user message to the local model.
- Render the full transcript in the mobile UI.
- Add a “New conversation / Clear chat” action.
- Do not store secrets in the chat log.

### Priority 3 — clean the model direction

- Replace or clearly deprecate the Qwen-based `train_agent.py`.
- Replace or clearly deprecate the old Transformers `train.py` and
  `evaluate.py`.
- Update `README.md` to make the scratch path the only recommended path.
- Keep The Pile as optional general-language pretraining data, not as the only
  tool-calling dataset.

### Priority 4 — expand the dataset

The current seed set is only a starting point. Add hundreds/thousands of
examples covering:

- Greetings and natural conversation.
- Follow-up questions using previous context.
- Arabic dialect variations.
- Arabic + English mixed commands.
- Correct tool selection.
- Tool arguments.
- Tool result interpretation.
- Missing files.
- Existing files and overwrite confirmation.
- Directory deletion and recursive confirmation.
- Invalid paths and path traversal attempts.
- “I don’t know” responses instead of hallucinating.
- Multi-step tasks:
  - read → summarize
  - create directory → write file
  - read → replace exact text

For every dangerous action, include examples where the model asks for
confirmation instead of executing immediately.

### Priority 5 — run smoke training

After PyTorch is installed, run a tiny CPU-only smoke test first:

```bash
python train_scratch.py \
  --data data/agent_instructions.jsonl \
  --output-dir model/scratch-smoke \
  --context 64 \
  --d-model 64 \
  --heads 4 \
  --layers 2 \
  --batch-size 2 \
  --gradient-accumulation 1 \
  --epochs 1 \
  --device cpu
```

Then evaluate:

```bash
python evaluate_scratch.py \
  --checkpoint model/scratch-smoke/final.pt \
  --data data/agent_instructions.jsonl \
  --device cpu
```

The active Python environment currently reported:

```text
torch unavailable: ModuleNotFoundError
```

Install dependencies using the project package manager before running the
training smoke test. Do not install packages with `apt-get`.

## 6. Run and test commands

Start the app:

```bash
python file-agent/app.py
```

Run the file-agent smoke test:

```bash
PYTHONPATH=file-agent python test_agent.py
```

Compile checks:

```bash
PYTHONPATH=file-agent python -m py_compile \
  file-agent/agent_loop.py \
  file-agent/app.py \
  file-agent/file_agent/*.py \
  file-agent/hwk_model/*.py \
  create_training_data.py \
  train_scratch.py \
  evaluate_scratch.py
```

Create/refresh seed data:

```bash
python create_training_data.py
```

Check the running application:

```bash
curl http://127.0.0.1:5000/
```

The configured Replit workflow is:

```text
Start application: python file-agent/app.py
```

## 7. Important safety rules

- Keep all file paths relative to the workspace.
- Do not allow absolute paths.
- Do not allow `../` escapes.
- Keep overwrite disabled unless the user explicitly confirms.
- Keep recursive directory deletion disabled unless explicitly confirmed.
- Do not place the model or datasets inside the file-agent workspace unless
  that is intentional; large artifacts should live outside it.
- Do not expose `SESSION_SECRET`, environment variables, or credentials.
- Do not add OpenRouter/Replit AI/OpenAI calls to the active scratch path.
- Do not download or train on all 30 Pile shards without checking disk space.
- Do not promise production-quality conversation from the current tiny seed
  dataset.

## 8. Product reality

A model trained from random weights will not immediately be a capable general
chatbot. The project needs:

1. A much larger Arabic/general-language corpus.
2. A carefully designed conversation and tool-calling dataset.
3. A tokenizer/context design suitable for Arabic.
4. Long GPU training.
5. Repeated evaluation and safety tests.

The current code is the foundation and experiment harness, not a finished
language model. The correct near-term goal is to prove the full loop:

```text
user message
→ scratch model
→ natural response OR JSON tool call
→ safe file tool
→ tool result
→ final natural response
```

## 9. Handoff acceptance checklist

Freebuff should not consider the handoff complete until:

- [ ] `agent_loop.py` loads `model/scratch/final.pt`.
- [ ] Flask maintains multi-turn conversation history.
- [ ] The UI shows a chat transcript on mobile.
- [ ] A greeting receives a model-generated response.
- [ ] A file request produces a JSON tool call.
- [ ] The tool executes inside the sandbox.
- [ ] The model receives the tool result.
- [ ] The final response accurately describes success/failure.
- [ ] Confirmation is required for overwrite and recursive deletion.
- [ ] `test_agent.py` covers the complete model → tool → response path.
- [ ] Scratch training and evaluation run on the RTX machine.
- [ ] No API key or hosted model is required at runtime.
