"""Model price table (USD per 1M tokens) and cost calculation."""

import logging

logger = logging.getLogger(__name__)

# model -> (input, cached input, output), USD per 1M tokens
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "text-embedding-3-small": (0.02, 0.0, 0.0),
}
FALLBACK_MODEL = "gpt-6.1-sol"


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    prices = PRICES.get(model)
    if prices is None:
        logger.warning("unknown model %r; pricing as %s", model, FALLBACK_MODEL)
        prices = PRICES[FALLBACK_MODEL]
    p_in, p_cached, p_out = prices
    uncached = max(input_tokens - cached_tokens, 0)
    total = (uncached * p_in + cached_tokens * p_cached + output_tokens * p_out) / 1_000_000
    return round(total, 6)
