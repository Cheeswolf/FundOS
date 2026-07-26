import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.analytics import (  # noqa: E402
    DatedPrice,
    align_prices_asof,
    calculate_nav,
    calculate_portfolio_returns,
    prices_to_returns,
)


class AnalyticsPropertyTests(unittest.TestCase):
    def test_weighted_return_stays_inside_asset_return_bounds(self) -> None:
        randomizer = random.Random(20260726)
        for _ in range(100):
            symbols = ["A", "B", "C", "D"]
            raw_weights = [randomizer.random() for _ in symbols]
            total = sum(raw_weights)
            weights = {
                symbol: raw / total
                for symbol, raw in zip(symbols, raw_weights, strict=True)
            }
            asset_returns = {
                symbol: [randomizer.uniform(-0.25, 0.25) for _ in range(30)]
                for symbol in symbols
            }
            portfolio = calculate_portfolio_returns(asset_returns, weights)
            for index, value in enumerate(portfolio):
                period = [asset_returns[symbol][index] for symbol in symbols]
                self.assertGreaterEqual(value + 1e-12, min(period))
                self.assertLessEqual(value - 1e-12, max(period))

    def test_positive_price_scaling_does_not_change_returns(self) -> None:
        randomizer = random.Random(42)
        for _ in range(100):
            prices = [100.0]
            for _ in range(50):
                prices.append(prices[-1] * (1 + randomizer.uniform(-0.05, 0.05)))
            scale = randomizer.uniform(0.01, 1000)
            original = prices_to_returns({"A": prices})["A"]
            scaled = prices_to_returns({"A": [value * scale for value in prices]})["A"]
            for left, right in zip(original, scaled, strict=True):
                self.assertAlmostEqual(left, right, places=12)

    def test_nav_is_exact_compounding_of_generated_returns(self) -> None:
        randomizer = random.Random(7)
        for _ in range(100):
            returns = [randomizer.uniform(-0.50, 0.50) for _ in range(60)]
            initial = randomizer.uniform(0.1, 10)
            nav = calculate_nav(returns, initial_nav=initial)
            expected = initial
            for index, periodic_return in enumerate(returns, start=1):
                expected *= 1 + periodic_return
                self.assertAlmostEqual(nav[index], expected, places=12)
                self.assertGreater(nav[index], 0)

    def test_asof_alignment_never_uses_a_future_observation(self) -> None:
        randomizer = random.Random(99)
        start = date(2026, 1, 1)
        for _ in range(50):
            prices: list[DatedPrice] = []
            source: dict[str, dict[date, float]] = {}
            for symbol in ("A", "B", "C"):
                source[symbol] = {}
                value = 100.0
                for index in range(40):
                    current = start + timedelta(days=index)
                    value *= 1 + randomizer.uniform(-0.02, 0.02)
                    if index in {0, 39} or randomizer.random() > 0.25:
                        source[symbol][current] = value
                        prices.append(DatedPrice(symbol, current, value))
            dates, aligned, _ = align_prices_asof(
                prices,
                ["A", "B", "C"],
                maximum_age_days=10,
            )
            for symbol in source:
                for index, valuation_date in enumerate(dates):
                    eligible = [
                        observed for observed in source[symbol]
                        if observed <= valuation_date
                    ]
                    source_date = max(eligible)
                    self.assertEqual(aligned[symbol][index], source[symbol][source_date])


if __name__ == "__main__":
    unittest.main()
