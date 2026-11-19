"""Tests for the model router — 12 pluggable model providers."""

import os
import sys
from pathlib import Path

import pytest

# Add file-agent to path so we can import model_router
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from file_agent.model_router import (
    PROVIDERS,
    provider_style,
    api_key_for,
    default_model_for,
    register_provider,
    dispatch,
    ProviderError,
)


def test_providers_registry_has_12_providers():
    """All 12 built-in providers must be registered."""
    assert len(PROVIDERS) == 12, f"Expected 12 providers, got {len(PROVIDERS)}"


def test_all_provider_names():
    """Verify expected provider names exist."""
    expected = {
        "openai", "openrouter", "anthropic", "gemini",
        "ollama", "groq", "together", "mistral",
        "deepseek", "perplexity", "ai21", "cohere",
    }
    actual = set(PROVIDERS.keys())
    assert actual == expected, f"Missing: {expected - actual}, Extra: {actual - expected}"


@pytest.mark.parametrize("provider", [
    "openai", "openrouter", "anthropic", "gemini",
    "ollama", "groq", "together", "mistral",
    "deepseek", "perplexity", "ai21", "cohere",
])
def test_provider_style(provider):
    """Each provider has a style."""
    style = provider_style(provider)
    assert style == "openai", f"{provider} style should be 'openai', got '{style}'"


@pytest.mark.parametrize("provider", [
    "openai", "openrouter", "anthropic", "gemini",
    "ollama", "groq", "together", "mistral",
    "deepseek", "perplexity", "ai21", "cohere",
])
def test_api_key_for(provider):
    """Each provider has an env-key name."""
    key = api_key_for(provider)
    # All should map to UPPER_CASE env var
    expected_env = provider.upper() + "_API_KEY"
    assert key == expected_env, f"{provider} api_key_for should return env name, got '{key}'"


@pytest.mark.parametrize("provider", [
    "openai", "openrouter", "anthropic", "gemini",
    "ollama", "groq", "together", "mistral",
    "deepseek", "perplexity", "ai21", "cohere",
])
def test_default_model_for(provider):
    """Each provider has a default model."""
    model = default_model_for(provider)
    assert isinstance(model, str) and len(model) > 0, f"{provider} should have a default model"


def test_register_provider():
    """register_provider adds a new provider dynamically."""
    register_provider("test_provider", "openai", "TEST_API_KEY", "test-model")
    assert "test_provider" in PROVIDERS
    assert PROVIDERS["test_provider"]["default_model"] == "test-model"
    # Clean up
    del PROVIDERS["test_provider"]


def test_dispatch_raises_for_unknown_provider():
    """dispatch raises ProviderError for unknown provider."""
    with pytest.raises(ProviderError):
        dispatch("test", "sys", "unknown_provider")  # type: ignore


def test_dispatch_raises_when_no_key_for_openai():
    """dispatch raises ProviderError when no API key is available for openai."""
    # openai has no key set in this env, so it should fail
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "openai")  # type: ignore


def test_dispatch_raises_when_no_key_for_ollama():
    """dispatch raises ProviderError when Ollama is not available."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "ollama")  # type: ignore


def test_dispatch_raises_when_no_key_for_groq():
    """dispatch raises ProviderError when no GROQ_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "groq")  # type: ignore


def test_dispatch_raises_when_no_key_for_together():
    """dispatch raises ProviderError when no TOGETHER_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "together")  # type: ignore


def test_dispatch_raises_when_no_key_for_mistral():
    """dispatch raises ProviderError when no MISTRAL_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "mistral")  # type: ignore


def test_dispatch_raises_when_no_key_for_deepseek():
    """dispatch raises ProviderError when no DEEPSEEK_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "deepseek")  # type: ignore


def test_dispatch_raises_when_no_key_for_perplexity():
    """dispatch raises ProviderError when no PERPLEXITY_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "perplexity")  # type: ignore


def test_dispatch_raises_when_no_key_for_ai21():
    """dispatch raises ProviderError when no AI21_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "ai21")  # type: ignore


def test_dispatch_raises_when_no_key_for_cohere():
    """dispatch raises ProviderError when no COHERE_API_KEY."""
    with pytest.raises(ProviderError):
        dispatch("test message", "you are a helpful assistant", "cohere")  # type: ignore