import pytest
from pydantic import ValidationError

from insightforge.config import Settings
from insightforge.core.costs import Price, estimate_cost, price_for

PRICES = {"openai/gpt-oss-20b": Price(input=0.075, output=0.30)}


def test_price_for_matches_the_exact_model_name_only():
    assert price_for("openai/gpt-oss-20b", PRICES) == PRICES["openai/gpt-oss-20b"]
    assert price_for("openai/gpt-oss-20b-mini", PRICES) is None
    assert price_for("OPENAI/GPT-OSS-20B", PRICES) is None
    assert price_for(None, PRICES) is None
    assert price_for("anything", {}) is None


def test_estimate_cost_uses_dollars_per_million_tokens():
    price = Price(input=0.075, output=0.30)
    assert estimate_cost(1_000_000, 1_000_000, price) == 0.375
    assert estimate_cost(1234, 567, price) == round((0.075 * 1234 + 0.30 * 567) / 1e6, 6)
    assert estimate_cost(0, 0, price) == 0.0
    assert estimate_cost(1000, 1000, None) is None


def test_price_rejects_negative_values():
    with pytest.raises(ValidationError):
        Price(input=-1, output=0)
    with pytest.raises(ValidationError):
        Price(input=0, output=-0.1)


def test_settings_parse_llm_prices_from_env_json(monkeypatch):
    monkeypatch.setenv("LLM_PRICES", '{"openai/gpt-oss-20b": {"input": 0.075, "output": 0.30}}')
    assert Settings().llm_prices == PRICES
    monkeypatch.delenv("LLM_PRICES")
    assert Settings(_env_file=None).llm_prices == {}
