import pytest

from insightforge.config import Settings
from insightforge.core.llm import (
    FakeLLMClient,
    OpenAICompatibleClient,
    build_llm,
    llm_mode,
    resolved_base_url,
    resolved_model,
)


def test_build_llm_without_credentials_uses_offline_fake(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    client = build_llm(Settings(_env_file=None, llm_api_key=None))
    assert isinstance(client, FakeLLMClient)
    assert llm_mode(client) == "fake"


def test_build_llm_allows_keyless_compatible_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    client = build_llm(
        Settings(
            _env_file=None,
            llm_api_key=None,
            llm_base_url="http://localhost:11434/v1",
        )
    )
    assert isinstance(client, OpenAICompatibleClient)
    assert llm_mode(client) == "openai"


def test_gemini_preset_resolves_base_url_and_model():
    settings = Settings(_env_file=None, llm_provider="gemini", llm_api_key="key")
    client = build_llm(settings)
    assert isinstance(client, OpenAICompatibleClient)
    assert resolved_base_url(settings) == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert resolved_model(settings) == "gemini-3.6-flash"
    assert client.model == "gemini-3.6-flash"


def test_explicit_model_overrides_provider_preset():
    settings = Settings(
        _env_file=None,
        llm_provider="gemini",
        llm_api_key="key",
        llm_model="gemini-custom",
    )
    assert resolved_model(settings) == "gemini-custom"
    assert build_llm(settings).model == "gemini-custom"


def test_openai_compatible_requires_base_url():
    settings = Settings(
        _env_file=None,
        llm_provider="openai-compatible",
        llm_api_key="key",
    )
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        build_llm(settings)


def test_ollama_uses_placeholder_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = Settings(_env_file=None, llm_provider="ollama", llm_api_key=None)
    client = build_llm(settings)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.client.api_key == "ollama"
    assert client.model == "llama3.1"
