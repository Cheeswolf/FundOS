import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.analytics import (  # noqa: E402
    DatedPrice,
    align_prices,
    align_prices_asof,
    prices_to_returns,
    returns_to_dated_nav,
)


class TimeSeriesTests(unittest.TestCase):
    def test_asof_alignment_carries_only_past_observations(self) -> None:
        dates, aligned, quality = align_prices_asof(
            [
                DatedPrice("A", date(2026, 7, 1), 100),
                DatedPrice("A", date(2026, 7, 2), 110),
                DatedPrice("A", date(2026, 7, 3), 121),
                DatedPrice("B", date(2026, 7, 1), 200),
                DatedPrice("B", date(2026, 7, 3), 220),
            ],
            ["A", "B"],
            maximum_age_days=2,
        )
        self.assertEqual(dates, [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)])
        self.assertEqual(aligned["B"], [200, 200, 220])
        self.assertEqual(quality.carried_values, 1)
        self.assertEqual(quality.maximum_age_days, 1)

    def test_asof_alignment_rejects_stale_carried_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "price for B is 2 days old"):
            align_prices_asof(
                [
                    DatedPrice("A", date(2026, 7, 1), 100),
                    DatedPrice("A", date(2026, 7, 3), 110),
                    DatedPrice("B", date(2026, 7, 1), 200),
                    DatedPrice("B", date(2026, 7, 4), 220),
                ],
                ["A", "B"],
                maximum_age_days=1,
            )

    def test_asof_alignment_uses_observation_before_common_start(self) -> None:
        dates, aligned, quality = align_prices_asof(
            [
                DatedPrice("A", date(2026, 6, 30), 100),
                DatedPrice("A", date(2026, 7, 3), 110),
                DatedPrice("B", date(2026, 7, 1), 200),
                DatedPrice("B", date(2026, 7, 3), 220),
            ],
            ["A", "B"],
            maximum_age_days=2,
        )
        self.assertEqual(dates, [date(2026, 7, 1), date(2026, 7, 3)])
        self.assertEqual(aligned["A"], [100, 110])
        self.assertEqual(quality.maximum_age_days, 1)

    def test_aligns_on_common_dates_and_calculates_returns(self) -> None:
        prices = [
            DatedPrice("A", date(2026, 7, 1), 100),
            DatedPrice("A", date(2026, 7, 2), 110),
            DatedPrice("A", date(2026, 7, 3), 121),
            DatedPrice("B", date(2026, 7, 1), 200),
            DatedPrice("B", date(2026, 7, 3), 220),
        ]
        dates, aligned = align_prices(prices, ["A", "B"])
        self.assertEqual(dates, [date(2026, 7, 1), date(2026, 7, 3)])
        returns = prices_to_returns(aligned)
        self.assertAlmostEqual(returns["A"][0], 0.21)
        self.assertAlmostEqual(returns["B"][0], 0.10)

    def test_rejects_insufficient_common_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "two common"):
            align_prices(
                [
                    DatedPrice("A", date(2026, 7, 1), 100),
                    DatedPrice("B", date(2026, 7, 1), 200),
                ],
                ["A", "B"],
            )

    def test_creates_dated_nav(self) -> None:
        nav = returns_to_dated_nav(
            [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
            [0.10, -0.10],
        )
        self.assertEqual(nav[-1].nav_date, date(2026, 7, 3))
        self.assertAlmostEqual(nav[-1].nav, 0.99)


if __name__ == "__main__":
    unittest.main()
