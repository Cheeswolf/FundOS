import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.analytics import TransactionCostPolicy, calculate_versioned_nav  # noqa: E402
from fundos.domain import Asset, InvestmentMandate, PortfolioProduct, PortfolioVersion, PositionWeight  # noqa: E402
from fundos.services import calculate_and_store_versioned_performance, publish_portfolio_version  # noqa: E402
from fundos.storage import Database  # noqa: E402


class VersionedPerformanceTests(unittest.TestCase):
    def versions(self) -> list[PortfolioVersion]:
        return [
            PortfolioVersion("V1", "P1", 1, date(2026, 7, 1), (PositionWeight("A", Decimal("1")),)),
            PortfolioVersion("V2", "P1", 2, date(2026, 7, 2), (PositionWeight("B", Decimal("1")),)),
        ]

    def test_switches_weights_after_effective_date(self) -> None:
        nav = calculate_versioned_nav(
            [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
            {"A": [100, 110, 110], "B": [100, 100, 120]},
            self.versions(),
        )
        self.assertAlmostEqual(nav[1].nav, 1.10)
        self.assertAlmostEqual(nav[2].nav, 1.32)

    def test_deducts_rebalance_cost_once_on_effective_date(self) -> None:
        nav = calculate_versioned_nav(
            [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
            {"A": [100, 110, 110], "B": [100, 100, 120]},
            self.versions(),
            transaction_cost_policy=TransactionCostPolicy(rate=0.01),
        )
        self.assertAlmostEqual(nav[1].nav, 1.10 * 0.99)
        self.assertAlmostEqual(nav[2].nav, 1.10 * 0.99 * 1.20)

    def test_can_charge_initial_allocation(self) -> None:
        nav = calculate_versioned_nav(
            [date(2026, 7, 1), date(2026, 7, 2)],
            {"A": [100, 110], "B": [100, 100]},
            self.versions(),
            transaction_cost_policy=TransactionCostPolicy(
                rate=0.01,
                charge_initial_allocation=True,
            ),
        )
        self.assertAlmostEqual(nav[0].nav, 0.99)
        self.assertAlmostEqual(nav[1].nav, 0.99 * 1.10 * 0.99)

    def test_rejects_invalid_transaction_cost_rate(self) -> None:
        with self.assertRaisesRegex(ValueError, "transaction cost rate"):
            TransactionCostPolicy(rate=1.0)

    def test_weekend_effective_version_starts_on_next_common_valuation_date(self) -> None:
        versions = [
            PortfolioVersion("V1", "P1", 1, date(2026, 7, 3), (PositionWeight("A", Decimal("1")),)),
            PortfolioVersion("V2", "P1", 2, date(2026, 7, 4), (PositionWeight("B", Decimal("1")),)),
        ]
        nav = calculate_versioned_nav(
            [date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7)],
            {"A": [100, 110, 110], "B": [100, 100, 120]},
            versions,
            transaction_cost_policy=TransactionCostPolicy(rate=0.01),
        )
        self.assertAlmostEqual(nav[1].nav, 1.10 * 0.99)
        self.assertAlmostEqual(nav[2].nav, 1.10 * 0.99 * 1.20)

    def test_persists_nav_and_performance_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([
                Asset("A", "Asset A", "equity"),
                Asset("B", "Asset B", "bond"),
                Asset("BM", "Benchmark", "benchmark"),
            ])
            database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))
            database.upsert_investment_mandate(
                InvestmentMandate("P1", "Test", "medium", Decimal("1"), Decimal("0"), Decimal("1"))
            )
            for version in self.versions():
                database.create_version(version)
                publish_portfolio_version(
                    database,
                    version_id=version.version_id,
                    reason="Test publication",
                    approved_by="test-committee",
                )
            dates = [
                date(2026, 6, 30),
                date(2026, 7, 1),
                date(2026, 7, 2),
                date(2026, 7, 3),
            ]
            series = {
                "A": [90, 100, 110, 110],
                "B": [90, 100, 100, 120],
                "BM": [90, 100, 105, 110],
            }
            rows = [
                ("fixture", symbol, nav_date, value)
                for symbol, values in series.items()
                for nav_date, value in zip(dates, values, strict=True)
            ]
            database.upsert_prices(rows)

            result = calculate_and_store_versioned_performance(
                database,
                product_id="P1",
                provider="fixture",
                benchmark_symbol="BM",
            )
            self.assertAlmostEqual(result.portfolio_nav, 1.32)
            self.assertAlmostEqual(result.benchmark_nav, 1.10)
            self.assertAlmostEqual(result.excess_return, 0.22)
            self.assertEqual(len(database.fetch_all("SELECT * FROM portfolio_nav")), 3)
            snapshots = database.fetch_all("SELECT * FROM performance_snapshots")
            self.assertEqual(len(snapshots), 1)
            self.assertAlmostEqual(snapshots[0]["excess_return"], 0.22)

    def test_service_applies_configured_transaction_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.sqlite3")
            database.initialize()
            database.upsert_assets([
                Asset("A", "Asset A", "equity"),
                Asset("B", "Asset B", "bond"),
                Asset("BM", "Benchmark", "benchmark"),
            ])
            database.create_product(PortfolioProduct("P1", "Portfolio", "BM", datetime.now()))
            database.upsert_investment_mandate(
                InvestmentMandate("P1", "Test", "medium", Decimal("1"), Decimal("0"), Decimal("1"))
            )
            for version in self.versions():
                database.create_version(version)
                publish_portfolio_version(
                    database,
                    version_id=version.version_id,
                    reason="Test publication",
                    approved_by="test-committee",
                )
            dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
            series = {
                "A": [100, 110, 110],
                "B": [100, 100, 120],
                "BM": [100, 105, 110],
            }
            database.upsert_prices([
                ("fixture", symbol, nav_date, value)
                for symbol, values in series.items()
                for nav_date, value in zip(dates, values, strict=True)
            ])

            result = calculate_and_store_versioned_performance(
                database,
                product_id="P1",
                provider="fixture",
                benchmark_symbol="BM",
                transaction_cost_rate=0.01,
            )
            self.assertAlmostEqual(result.portfolio_nav, 1.10 * 0.99 * 1.20)


if __name__ == "__main__":
    unittest.main()
