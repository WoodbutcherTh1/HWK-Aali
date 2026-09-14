from __future__ import annotations

import torch

from hwk_model.model import TinyCausalLM
from hwk_model.bpe_tokenizer import BpeTokenizer


@torch.no_grad()
def generate_text(
    model: TinyCausalLM,
    tokenizer: BpeTokenizer,
    prompt: str,
    *,
    max_new_tokens: int = 160,
    temperature: float = 0.7,
    top_k: int = 40,
    repetition_penalty: float = 1.15,
    ban_unk: bool = True,
) -> str:
    """Sample a continuation for `prompt`.

    2026-09-14 lessons (Phase C run-3 smoke): the runtime serves with greedy
    decoding (temperature=0) and a 110M model under greedy decoding walks
    straight into token loops ("وَلكِنْ وَلكِنْ وَلكِنْ", "::::"). A repetition
    penalty over the ALREADY-GENERATED window breaks those attractors while
    leaving single-turn answers untouched. Separately, `⁇` in the smoke output
    is sentencepiece's literal rendering of the <unk> id - a model should
    never SPEAK unk, so it is banned from sampling by default (ban_unk=False
    restores the old behavior; keep True everywhere).

    Greedy stays greedy (temperature<=0 uses argmax; the penalty still applies
    to the logits BEFORE the argmax, so loops break there too).
    """
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
    # The penalty applies to the tokens the MODEL produces in this call, not
    # to the prompt the user wrote (penalizing the prompt would push the model
    # away from quoting/echoing the user's own words).
    penalty_window: list[int] = []
    for _ in range(max_new_tokens):
        input_ids = torch.tensor([generated[-context_size:]], dtype=torch.long, device=device)
        logits = model(input_ids)[:, -1, :]
        if repetition_penalty and repetition_penalty != 1.0 and len(penalty_window) > 0:
            window = torch.tensor(penalty_window, dtype=torch.long, device=device)
            gathered = logits[0, window]
            # Divide positive logits, multiply negative ones - the standard
            # repetition penalty (CTRL paper) so both directions are pushed
            # DOWN, never flipped upward.
            logits[0, window] = torch.where(
                gathered > 0,
                gathered / repetition_penalty,
                gathered * repetition_penalty,
            )
        if ban_unk and tokenizer.unk_id >= 0:
            logits[0, tokenizer.unk_id] = float("-inf")
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
        penalty_window.append(token)
        if len(penalty_window) > context_size:
            penalty_window.pop(0)
        if token == tokenizer.eos_id:
            break
    return tokenizer.decode(generated[len(tokens):]).strip()
