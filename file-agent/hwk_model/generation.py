"""Text generation for the from-scratch HWK model."""

from __future__ import annotations

import torch

from .model import TinyCausalLM
from .tokenizer import ByteTokenizer


@torch.no_grad()
def generate_text(
    model: TinyCausalLM,
    tokenizer: ByteTokenizer,
    prompt: str,
    *,
    max_new_tokens: int = 160,
    temperature: float = 0.7,
    top_k: int = 40,
) -> str:
    device = next(model.parameters()).device
    # add_special_tokens=False + manual BOS: tokenizer.encode(..., add_special_tokens=True)
    # (the default) appends an EOS right after the prompt, which made every
    # continuation start from a context that already looked "finished" to the
    # model and collapsed generation to an immediate EOS (empty output). We
    # want BOS at the start (matching how training sequences begin) but no
    # EOS in the middle - the model should produce EOS itself once it is
    # actually done generating.
    tokens = [tokenizer.bos_id, *tokenizer.encode(prompt, add_special_tokens=False)]
    context_size = model.config.context_size
    generated = list(tokens)
    for _ in range(max_new_tokens):
        input_ids = torch.tensor([generated[-context_size:]], dtype=torch.long, device=device)
        logits = model(input_ids)[:, -1, :]
        if temperature <= 0:
            next_token = logits.argmax(dim=-1)
        else:
            logits = logits / temperature
            if top_k > 0:
                values, indices = torch.topk(logits, min(top_k, logits.shape[-1]))
                filtered = torch.full_like(logits, float("-inf"))
                filtered.scatter_(1, indices, values)
                logits = filtered
            next_token = torch.multinomial(torch.softmax(logits, dim=-1), num_samples=1).squeeze(1)
        token = int(next_token.item())
        generated.append(token)
        if token == tokenizer.eos_id:
            break
    return tokenizer.decode(generated[len(tokens):]).strip()