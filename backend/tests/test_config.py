from insightforge.config import Settings, validate_settings


def test_development_reports_secret_problems_without_rejecting():
    settings = Settings(_env_file=None, environment="development")
    problems = validate_settings(settings)
    assert any("JWT_SECRET" in problem for problem in problems)
    assert any("APP_SECRET" in problem for problem in problems)


def test_production_rejects_default_secrets_and_private_urls():
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="change-me",
        app_secret="short",
        allow_private_urls=True,
        llm_provider="fake",
    )
    problems = validate_settings(settings)
    assert any("JWT_SECRET" in problem for problem in problems)
    assert any("APP_SECRET" in problem for problem in problems)
    assert any("ALLOW_PRIVATE_URLS" in problem for problem in problems)
    assert not any("LLM" in problem for problem in problems)


def test_local_model_must_run_on_this_computer():
    remote = Settings(
        _env_file=None, local_llm_model="qwen3:4b", local_llm_base_url="http://192.168.1.20:11434"
    )
    assert any("LOCAL_LLM_BASE_URL" in problem for problem in validate_settings(remote))
    for url in ("http://localhost:11434", "http://127.0.0.1:11434/v1", "http://[::1]:11434"):
        local = Settings(_env_file=None, local_llm_model="qwen3:4b", local_llm_base_url=url)
        assert not any("LOCAL_LLM" in problem for problem in validate_settings(local))
