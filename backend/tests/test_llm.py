import json

import httpx
import pytest
import respx

from insightforge.config import Settings
from insightforge.core.llm import (
    FakeLLMClient,
    OllamaClient,
    OpenAICompatibleClient,
    build_llm,
    build_local_llm,
    llm_mode,
    resolved_base_url,
    resolved_model,
)


@respx.mock
def test_ollama_client_bounds_the_context_and_parses_json():
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": '{"n": 42}', "thinking": "private"},
                "prompt_eval_count": 12,
                "eval_count": 5,
            },
        )
    )
    client = OllamaClient("qwen3:4b", "http://localhost:11434/v1", context_tokens=4096, max_output_tokens=256)
    parsed, response = client.chat_json([{"role": "user", "content": "sum"}])
    assert parsed == {"n": 42}
    assert (response.prompt_tokens, response.completion_tokens) == (12, 5)
    body = json.loads(route.calls.last.request.content)
    assert (body["options"]["num_ctx"], body["options"]["num_predict"]) == (4096, 256)
    assert (body["think"], body["format"], body["stream"]) == (False, "json", False)


@respx.mock
def test_ollama_client_reports_server_errors():
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(500, json={"error": "out of memory"})
    )
    with pytest.raises(RuntimeError, match="out of memory"):
        OllamaClient("qwen3:4b").chat([{"role": "user", "content": "hello"}])


def test_build_local_llm_requires_a_model_on_this_computer():
    assert build_local_llm(Settings(_env_file=None, local_llm_model=None)) is None
    remote = Settings(
        _env_file=None, local_llm_model="qwen3:4b", local_llm_base_url="http://10.0.0.5:11434"
    )
    assert build_local_llm(remote) is None
    client = build_local_llm(Settings(_env_file=None, local_llm_model="qwen3:4b"))
    assert isinstance(client, OllamaClient)
    assert client.model == "qwen3:4b"


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
