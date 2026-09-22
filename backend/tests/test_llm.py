from insightforge.config import Settings
from insightforge.core.llm import FakeLLMClient, OpenAICompatibleClient, build_llm, llm_mode


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
