# model_router integration report

## Files changed
- `file-agent/file_agent/model_router/__init__.py` — rewrote the provider registry and dispatch path.
- `file-agent/model_router/__init__.py` — empty helper shim added alongside the real package.

## What landed
- 12 providers registered in `PROVIDERS` with explicit `style`, `env_key`, `default_model`, and `base_url`.
- `perplexity` now uses `base_url = https://api.perplexity.ai` instead of the duplicated `.../chat/completions` path.
- `groq` default model is `llama-3.1-8b-instant`.
- `dispatch()` builds the chat/completions URL from each provider's `base_url` + `/chat/completions`.
- The old `_KNOWN_URLS` helper was removed before the `ProviderError` class was defined, so there is no hanging reference.

## Verification
- Import check: `ProviderError` is defined, `PROVIDERS` has 12 entries, perplexity/groq values print correctly.
- Dispatch contract: calling `dispatch` without any API key now fails fast with `ProviderError("no API key configured for …")`.
- A live Ollama call fails for the right reason (the local Ollama is missing the requested model), not because of routing or signature bugs.

## Notes
- `file-agent/app.py` already references `aali_model_router` in imports and in `/api/model/router` + `/api/model/status`, so the package is wired into the server namespace as expected.
- No API keys were added anywhere.
