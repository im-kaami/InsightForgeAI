from insightforge.config import Price


def price_for(model: str | None, prices: dict[str, Price]) -> Price | None:
    if not model:
        return None
    return prices.get(model)


def estimate_cost(prompt_tokens: int, completion_tokens: int, price: Price | None) -> float | None:
    if price is None:
        return None
    return round((price.input * prompt_tokens + price.output * completion_tokens) / 1e6, 6)
