import logging

from app.agents.runtime.pricing import PRICES, cost_usd


def test_cost_nano():
    assert cost_usd("gpt-5-nano", 1_000_000, 0, 1_000_000) == 0.45


def test_cost_cached():
    assert cost_usd("gpt-6-luna", 1_000_000, 500_000, 0) == 0.055


def test_unknown_model_priced_as_sol_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        cost = cost_usd("mystery-model", 1_000_000, 0, 0)
    assert cost == cost_usd("gpt-6.1-sol", 1_000_000, 0, 0) == 2.0
    assert any("mystery-model" in r.message for r in caplog.records)
    assert "gpt-6.1-sol" in PRICES
