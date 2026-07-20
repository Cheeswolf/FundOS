import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.analytics.metrics import (  # noqa: E402
    calculate_maximum_drawdown,
    calculate_metrics,
    calculate_nav,
    calculate_portfolio_returns,
)


class PortfolioAnalyticsTests(unittest.TestCase):
    def test_calculates_weighted_returns(self) -> None:
        result = calculate_portfolio_returns(
            {"EQUITY": [0.10, -0.10], "BOND": [0.02, 0.02]},
            {"EQUITY": 0.6, "BOND": 0.4},
        )
        self.assertAlmostEqual(result[0], 0.068)
        self.assertAlmostEqual(result[1], -0.052)

    def test_rejects_invalid_weights(self) -> None:
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            calculate_portfolio_returns({"A": [0.01]}, {"A": 0.9})

    def test_compounds_nav(self) -> None:
        nav = calculate_nav([0.10, -0.10])
        self.assertEqual(len(nav), 3)
        self.assertAlmostEqual(nav[-1], 0.99)

    def test_calculates_maximum_drawdown(self) -> None:
        drawdown = calculate_maximum_drawdown([1.0, 1.2, 0.9, 1.1])
        self.assertAlmostEqual(drawdown, -0.25)

    def test_calculates_complete_metrics(self) -> None:
        metrics = calculate_metrics([0.01, -0.02, 0.03], periods_per_year=12)
        self.assertEqual(metrics.observations, 3)
        self.assertAlmostEqual(metrics.cumulative_return, 1.01 * 0.98 * 1.03 - 1)
        self.assertLess(metrics.maximum_drawdown, 0)
        self.assertIsNotNone(metrics.sharpe_ratio)

    def test_constant_returns_have_no_sharpe_ratio(self) -> None:
        metrics = calculate_metrics([0.01, 0.01], periods_per_year=12)
        self.assertEqual(metrics.annualized_volatility, 0)
        self.assertIsNone(metrics.sharpe_ratio)


if __name__ == "__main__":
    unittest.main()

